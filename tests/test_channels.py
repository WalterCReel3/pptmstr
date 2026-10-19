"""
The link logs: the seam's contract, the file implementation's own edges, the
fold, the startup read, what the store's steps write, and the appender.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from pptmstr import boardlog, channels
from pptmstr.boardlog import RecordingStopped, Unlisted, Unrecognised
from pptmstr.channels import (
    Appended,
    ChannelLog,
    FileChannels,
    LinkClosed,
    LinkCreated,
    LinkKey,
    NotAppended,
    Read,
    Short,
    Stopped,
    Swept,
    Whole,
)
from pptmstr.intents import AgentSpawned, ChannelClosed, ChannelLinked
from pptmstr.model import Snapshot
from pptmstr.store import Store

A, B, C = "sess-a", "sess-b", "sess-c"
K1 = LinkKey("brand", "k1")


def _created(
    channel_id: str = "k1",
    at: float = 1.0,
    charter: str = "the brand work",
    members: tuple[str, ...] = (A, B),
) -> LinkCreated:
    return LinkCreated(at=at, channel_id=channel_id, members=members, charter=charter)


def _newer(kind: str = "link-widened", at: float = 3.0) -> Unrecognised:
    """A record of a kind a later build defines and this one does not."""
    text = json.dumps({"kind": kind, "at": at, "channel_id": "k1", "members": [A, B, C]})
    return Unrecognised(at=at, kind=kind, text=text)


def _unlistable() -> bool:
    """Whether a directory without read permission refuses a listing here."""
    return os.geteuid() != 0


# -- the contract every implementation passes ---------------------------------------

# Each entry opens a log on the directory it is handed; two opened on one
# directory are two windows on one store. A second backend is added here and
# nowhere else: the contract below is its test.
_IMPLEMENTATIONS: dict[str, Callable[[Path], ChannelLog]] = {
    "file": FileChannels,
}


@pytest.fixture(params=sorted(_IMPLEMENTATIONS))
def opened(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Callable[[], ChannelLog]]:
    """Opens another log on one fresh, empty store each time it is called."""
    yield lambda: _IMPLEMENTATIONS[request.param](tmp_path / "channels")


def test_the_seam_keeps_facts_in_order_per_link_and_says_what_it_holds(
    opened: Callable[[], ChannelLog],
) -> None:
    """
    The contract, whole, in one test so a second backend is held to every line:

    - a link with no log is empty, whole and unlisted;
    - appended facts read back in the order appended, per link, and a fact on
      one link is never on another, under the same name or not;
    - ``links`` is every link with a log, sorted;
    - a closed link stays in the log: closing appends, it removes nothing;
    - a key that is not a plain name and id has no log;
    - ``state`` is ``judge`` of ``read``;
    - a fact of a kind this build does not define is carried, and the log is
      short; the writer's stop marker makes it stopped;
    - facts are kept as appended: one whose time runs backwards stays where
      it was put, and two equal facts are two facts, appended one after the
      other or apart.
    """
    log = opened()
    assert log.links() == ()
    assert log.read(K1) == Read()
    assert log.state(K1) == Whole()

    created, closed = _created(), LinkClosed(at=2.0, channel_id="k1")
    again = LinkKey("brand", "k2")
    other = LinkKey("ops", "k9")
    assert log.append(K1, created) == Appended()
    assert log.append(other, _created("k9")) == Appended()
    assert log.append(K1, closed) == Appended()
    assert log.append(again, _created("k2", 3.0)) == Appended()

    assert log.read(K1) == Read((created, closed))
    assert log.read(again) == Read((_created("k2", 3.0),))
    assert log.read(other) == Read((_created("k9"),))
    assert log.links() == (K1, again, other)
    assert log.links("brand") == (K1, again)
    assert log.links("none") == () == log.links("../brand")
    for link in (K1, again, other):
        assert log.state(link) == Whole() == channels.judge(link, log.read(link))

    bad_keys = [LinkKey(n, "k1") for n in ("", "Brand", "../brand", ".brand", "a" * 65)]
    bad_keys += [LinkKey("brand", i) for i in ("", "../k1", "K1", "k 1")]
    for bad in bad_keys:
        assert isinstance(log.append(bad, created), NotAppended), bad
        assert log.read(bad).facts == ()
    assert log.links() == (K1, again, other)

    newer = _newer()
    assert log.append(K1, newer) == Appended()
    assert log.read(K1).facts == (created, closed, newer)
    state = log.state(K1)
    assert isinstance(state, Short) and "link-widened" in state.reason

    stop = RecordingStopped(at=4.0, reason="the disk was full")
    assert log.append(other, stop) == Appended()
    assert log.state(other) == Stopped("its writer stopped: the disk was full")

    # Backwards in time, and the same fact twice: kept as appended. The second
    # creation is contested, which is the fold's to say, not the seam's to drop.
    late = LinkKey("late", "k5")
    closed_early = LinkClosed(at=1.0, channel_id="k5")
    facts = (_created("k5", 9.0), closed_early, closed_early, _created("k5", 9.0))
    for fact in facts:
        assert log.append(late, fact) == Appended()
    assert log.read(late) == Read(facts)
    assert isinstance(log.state(late), Short)


def test_two_writers_appending_to_one_link_lose_nothing_and_leave_no_gap(
    opened: Callable[[], ChannelLog],
) -> None:
    """
    boardlog's idiom, ported into the contract: eight writers -- each its own
    log, as eight windows would be -- appending to one link at once. Every
    fact is there, in each writer's own order, and the log reads to its end.
    """
    writers, each = 8, 40
    start = threading.Barrier(writers)
    failures: list[object] = []

    def run(who: int) -> None:
        mine = opened()
        start.wait()
        for n in range(each):
            appended = mine.append(K1, LinkClosed(at=float(n), channel_id=f"w{who}"))
            if not isinstance(appended, Appended):
                failures.append(appended)
                return

    threads = [threading.Thread(target=run, args=(who,)) for who in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    read = opened().read(K1)
    assert failures == []
    assert read.broken is None
    assert len(read.facts) == writers * each
    for who in range(writers):
        mine = [f.at for f in read.facts if isinstance(f, LinkClosed) and f.channel_id == f"w{who}"]
        assert mine == [float(n) for n in range(each)]


# -- the file implementation --------------------------------------------------------


def test_one_link_is_one_directory_under_its_name(tmp_path: Path) -> None:
    FileChannels(tmp_path).append(K1, _created())

    assert sorted(p.name for p in (tmp_path / "brand" / "k1").iterdir()) == ["00000000.json"]


def test_a_record_of_a_kind_this_build_does_not_define_makes_the_log_short(
    tmp_path: Path,
) -> None:
    """
    Written by hand as a newer build would write it, beside a link this build
    reads: the record is carried, the log is short, and the link is read but
    withheld from the store.
    """
    log = FileChannels(tmp_path)
    assert log.append(K1, _created()) == Appended()
    (tmp_path / "brand" / "k1" / "00000001.json").write_text(
        json.dumps({"kind": "member-added", "at": 2.0, "channel_id": "k1", "member": C}) + "\n"
    )

    facts = log.read(K1).facts
    assert facts[0] == _created()
    assert isinstance(facts[1], Unrecognised) and facts[1].kind == "member-added"
    state = log.state(K1)
    assert isinstance(state, Short) and "member-added" in state.reason
    restored = channels.restore_all(log)
    assert not isinstance(restored, Unlisted)
    (only,) = restored
    assert only.channel is not None and only.channel.live
    assert only.withheld is not None and "member-added" in only.withheld


def test_a_board_record_in_a_links_directory_is_of_no_kind_a_link_has(tmp_path: Path) -> None:
    boardlog.append(tmp_path / "brand" / "k1", boardlog.Claimed(at=1.0, task_id="t", by="w"))

    facts = FileChannels(tmp_path).read(K1).facts

    assert len(facts) == 1 and isinstance(facts[0], Unrecognised)
    assert facts[0].kind == boardlog.Claimed.KIND
    assert isinstance(FileChannels(tmp_path).state(K1), Short)


def _write(directory: Path, ordinal: int, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{ordinal:08d}.json").write_text(text + "\n")


def test_a_link_fact_of_the_wrong_shape_ends_the_read_and_says_what_is_withheld(
    tmp_path: Path,
) -> None:
    """
    A channel-level break withholds the records after it, and says how many,
    in boardlog's words: two readable ones here, and the undecodable file at
    the boardlog-level break after them with its own one behind it.
    """
    log = FileChannels(tmp_path)
    directory = tmp_path / "brand" / "k1"
    _write(directory, 0, json.dumps({"kind": "link-created", "at": 1.0, "channel_id": "k1"}))
    for fact in (LinkClosed(at=2.0, channel_id="k1"), _created("k1")):
        assert log.append(K1, fact) == Appended()

    read = log.read(K1)
    assert read.facts == ()
    assert read.broken is not None and "members" in read.broken
    assert read.broken.endswith("and 2 after it are withheld")

    _write(directory, 3, "not a record")
    _write(directory, 4, "nor this")
    assert log.read(K1).broken.endswith("and 4 after it are withheld")  # type: ignore[union-attr]


def test_a_missing_ordinal_ends_the_read_and_says_what_is_withheld(tmp_path: Path) -> None:
    log = FileChannels(tmp_path)
    for fact in (_created(), LinkClosed(at=2.0, channel_id="k1"), _newer()):
        assert log.append(K1, fact) == Appended()
    (tmp_path / "brand" / "k1" / "00000001.json").unlink()

    read = log.read(K1)
    assert read.facts == (_created(),)
    assert read.broken == "record 1 cannot be read: missing, and 1 after it are withheld"
    # A malformed record ahead of a missing ordinal counts the files past the
    # gap, and no file for the gap itself.
    _write(tmp_path / "brand" / "k1", 0, json.dumps({"kind": "link-created"}))
    assert log.read(K1).broken.endswith("and 1 after it are withheld")  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "damage",
    [
        "not JSON at all",
        json.dumps({"kind": "link-created", "at": 2, "channel_id": "k1", "members": "ab"}),
    ],
    ids=["undecodable", "malformed-link"],
)
def test_a_close_behind_damage_cannot_leave_the_link_live(tmp_path: Path, damage: str) -> None:
    """
    A link, a damaged record, then a close. ``boardlog.append`` sees no gap
    behind a record that is present and does not decode, so the close is
    reported appended, and no read reaches it. The link must still not come
    back live: the log is short, so the link is withheld from the store.
    """
    log = FileChannels(tmp_path)
    assert log.append(K1, _created()) == Appended()
    _write(tmp_path / "brand" / "k1", 1, damage)
    assert log.append(K1, LinkClosed(at=3.0, channel_id="k1")) == Appended()

    assert log.read(K1).facts == (_created(),)
    restored = channels.restore_all(log)

    assert not isinstance(restored, Unlisted)
    (only,) = restored
    assert isinstance(only.state, Short) and "withheld" in only.state.reason
    assert only.withheld is not None


def test_links_skips_what_is_not_a_links_directory(tmp_path: Path) -> None:
    (tmp_path / "Upper" / "k1").mkdir(parents=True)
    (tmp_path / ".hidden" / "k1").mkdir(parents=True)
    (tmp_path / "file").write_text("")
    (tmp_path / "brand" / "k1").mkdir(parents=True)
    (tmp_path / "brand" / ".tmp").mkdir()
    (tmp_path / "empty").mkdir()

    assert FileChannels(tmp_path).links() == (K1,)
    assert FileChannels(tmp_path / "missing").links() == ()


@pytest.mark.skipif(not _unlistable(), reason="root lists any directory")
@pytest.mark.parametrize("where", ["root", "name"])
def test_a_store_that_cannot_be_listed_is_not_one_with_no_links(tmp_path: Path, where: str) -> None:
    """
    "Could not look" and "none" are different answers: the first, taken for
    the second, would let a name be linked while a live link under it sat
    unseen.
    """
    log = FileChannels(tmp_path / "channels")
    assert log.append(K1, _created()) == Appended()
    locked = log.root if where == "root" else log.root / "brand"
    locked.chmod(0o300)
    try:
        listed = log.links()
        restored = channels.restore_all(log)
    finally:
        locked.chmod(0o700)

    assert isinstance(listed, Unlisted) and "PermissionError" in listed.reason
    assert restored == listed


def test_the_default_root_is_under_the_configuration_directory_and_not_under_boards() -> None:
    from pptmstr import boardsync, settings

    assert channels.root().name == "channels"
    assert FileChannels().root == channels.root()
    assert settings.config_dir() / "boards" == boardsync.legacy_root()
    assert channels.root().parent == boardsync.legacy_root().parent


def test_a_charter_round_trips_byte_for_byte(tmp_path: Path) -> None:
    charter = 'quotes " and \\ backslash, a lone \udc80 surrogate, \x00 and\nnewline'
    log = FileChannels(tmp_path)
    assert log.append(K1, _created(charter=charter)) == Appended()

    (read,) = log.read(K1).facts
    assert isinstance(read, LinkCreated) and read.charter == charter


def test_the_longest_charter_of_the_costliest_characters_still_fits_a_record(
    tmp_path: Path,
) -> None:
    """``CHARTER_CHARS``' reason: what ``app.link_leads`` lets through always lands."""
    for costly in ("\U0001f600", "\udc80", "\x00", '"', "\\"):
        charter = costly * channels.CHARTER_CHARS
        size = len(boardlog.encode(channels.encode(_created(charter=charter))).encode("utf-8"))
        assert size < boardlog.MAX_RECORD_BYTES // 4, costly
        assert (
            FileChannels(tmp_path).append(
                LinkKey("brand", f"k{ord(costly)}"), _created(f"k{ord(costly)}", charter=charter)
            )
            == Appended()
        )


# -- the fold -----------------------------------------------------------------------


def test_the_fold_closes_the_link_and_a_second_close_agrees() -> None:
    """Two windows that each closed the link agree; closing is idempotent."""
    facts = (
        _created("k1", 1.0),
        LinkClosed(at=2.0, channel_id="k1"),
        LinkClosed(at=3.0, channel_id="k1"),
    )
    folded = channels.restore(K1, facts)

    assert folded.channel is not None
    assert (folded.channel.name, folded.channel.closed_at) == ("brand", 2.0)
    assert folded.channel.members == frozenset({A, B})
    assert channels.assess(folded) == Whole()


@pytest.mark.parametrize(
    ("facts", "field"),
    [
        # Two windows each wrote the creation.
        ((_created("k1"), _created("k1", 2.0)), "contested"),
        # A fact about a link this log is not.
        ((_created("k1"), _created("k2")), "stray"),
        ((_created("k1"), LinkClosed(at=2.0, channel_id="k2")), "stray"),
        # A close before anything was created.
        ((LinkClosed(at=1.0, channel_id="k1"),), "stray"),
    ],
)
def test_a_fact_one_reducer_could_not_have_appended_makes_the_log_short(
    facts: tuple[channels.Fact, ...], field: str
) -> None:
    folded = channels.restore(K1, facts)

    assert len(getattr(folded, field)) == 1
    assert folded.channel is None or folded.channel.id == "k1"
    assert isinstance(channels.assess(folded), Short)


@pytest.mark.parametrize("members", [(A,), (A, A), (A, B, C), ()])
def test_a_link_that_is_not_a_pair_is_not_restored(members: tuple[str, ...]) -> None:
    """Decided 7 on restore: a record of a link wider than a pair makes nothing live."""
    folded = channels.restore(K1, (_created(members=members),))

    assert folded.channel is None
    state = channels.assess(folded)
    assert isinstance(state, Short)
    assert f"a link of {len(set(members))} member(s), where a link joins 2" in state.reason


# -- the startup read ---------------------------------------------------------------


def _restored(log: ChannelLog) -> dict[LinkKey, channels.Restored]:
    restored = channels.restore_all(log)
    assert not isinstance(restored, Unlisted)
    return {r.link: r for r in restored}


def test_a_name_is_linked_again_under_a_fresh_log_whatever_became_of_the_last(
    tmp_path: Path,
) -> None:
    """
    A closed link and a stopped one under "brand" leave the name free: the
    one live link under it is restored, and neither earlier log stands in its
    way.
    """
    log = FileChannels(tmp_path)
    stopped, live = LinkKey("brand", "k2"), LinkKey("brand", "k3")
    for link, fact in (
        (K1, _created("k1")),
        (K1, LinkClosed(at=2.0, channel_id="k1")),
        (stopped, _created("k2", 3.0)),
        (stopped, RecordingStopped(at=4.0, reason="disk full")),
        (live, _created("k3", 5.0)),
    ):
        assert log.append(link, fact) == Appended()

    restored = _restored(log)

    closed, made = restored[K1].channel, restored[live].channel
    assert restored[K1].withheld is None and closed is not None and not closed.live
    assert restored[stopped].withheld == "its log was stopped: its writer stopped: disk full"
    assert restored[live].withheld is None and made is not None and made.live


def test_two_live_links_under_one_name_are_both_withheld(tmp_path: Path) -> None:
    """Two windows each linked the name; neither is trusted over the other."""
    log = FileChannels(tmp_path)
    other = LinkKey("brand", "k2")
    assert log.append(K1, _created("k1")) == Appended()
    assert log.append(other, _created("k2")) == Appended()
    assert log.append(LinkKey("ops", "k3"), _created("k3")) == Appended()

    restored = _restored(log)

    for link in (K1, other):
        assert restored[link].state == Whole()
        assert restored[link].withheld == "2 links under #brand are live at once"
    assert restored[LinkKey("ops", "k3")].withheld is None


def test_an_empty_links_directory_restores_nothing(tmp_path: Path) -> None:
    (tmp_path / "brand" / "k1").mkdir(parents=True)

    assert _restored(FileChannels(tmp_path))[K1].withheld == "its log holds no link"


def test_startup_reads_each_log_once(tmp_path: Path) -> None:
    """A log's links and its state come from one read, so they cannot disagree."""
    inner = FileChannels(tmp_path)
    for link in (K1, LinkKey("ops", "k2")):
        assert inner.append(link, _created(link.link_id)) == Appended()
    asked: list[str] = []

    class Counting:
        def links(self, name: str | None = None) -> tuple[LinkKey, ...] | Unlisted:
            asked.append("links")
            return inner.links(name)

        def append(self, link: LinkKey, fact: channels.Fact) -> Appended | NotAppended:
            raise AssertionError("startup appends nothing")

        def read(self, link: LinkKey) -> Read:
            asked.append(f"read {link.name}")
            return inner.read(link)

        def state(self, link: LinkKey) -> channels.ChannelState:
            asked.append(f"state {link.name}")
            return inner.state(link)

    channels.restore_all(Counting())

    assert asked == ["links", "read brand", "read ops"]


# -- what the store's steps write ---------------------------------------------------


def _running(store: Store, *sessions: str) -> None:
    for sid in sessions:
        store.apply(AgentSpawned((sid, None), None, "lead", "m", 0.0))


def _linked(channel_id: str = "k1", recording: frozenset[str] = frozenset({A, B})) -> ChannelLinked:
    return ChannelLinked(channel_id, "brand", frozenset({A, B}), "the brand work", 10.0, recording)


def _step(store: Store, intent: object) -> tuple[Snapshot, Snapshot]:
    before = store.snapshot()
    store.apply(intent)  # type: ignore[arg-type]
    return before, store.snapshot()


def test_a_link_and_its_close_are_the_facts_the_store_settled() -> None:
    store = Store()
    _running(store, A, B)

    before, after = _step(store, _linked())
    assert channels.outcomes(before, _linked(), after) == (
        (K1, LinkCreated(10.0, "k1", (A, B), "the brand work")),
    )

    closing = ChannelClosed("k1", 20.0)
    before, after = _step(store, closing)
    assert channels.outcomes(before, closing, after) == ((K1, LinkClosed(20.0, "k1")),)

    # Closed already: nothing more is written.
    before, after = _step(store, ChannelClosed("k1", 30.0))
    assert channels.outcomes(before, ChannelClosed("k1", 30.0), after) == ()


def test_a_refused_link_writes_nothing() -> None:
    store = Store()
    _running(store, A, B)
    refused = _linked(recording=frozenset({A}))

    before, after = _step(store, refused)

    assert after.channels == {}
    assert channels.outcomes(before, refused, after) == ()


# -- the appender -------------------------------------------------------------------


class _Failing:
    """A log whose first append of a fact to one link fails; every other lands."""

    def __init__(self, inner: ChannelLog, failing: LinkKey) -> None:
        self.inner, self.failing = inner, failing
        self.fail = True

    def links(self, name: str | None = None) -> tuple[LinkKey, ...] | Unlisted:
        return self.inner.links(name)

    def append(self, link: LinkKey, fact: channels.Fact) -> Appended | NotAppended:
        if link == self.failing and self.fail and not isinstance(fact, RecordingStopped):
            self.fail = False
            return NotAppended("no space left on device")
        return self.inner.append(link, fact)

    def read(self, link: LinkKey) -> Read:
        return self.inner.read(link)

    def state(self, link: LinkKey) -> channels.ChannelState:
        return self.inner.state(link)


def test_the_writer_appends_off_the_calling_thread_and_reports_each_fact(tmp_path: Path) -> None:
    log = FileChannels(tmp_path)
    writer = channels.ChannelWriter(log)
    writer.submit(K1, _created())
    writer.submit(K1, LinkClosed(at=2.0, channel_id="k1"))

    reports = writer.close()

    assert [type(r.result) for r in reports] == [Appended, Appended]
    assert log.read(K1).facts == (_created(), LinkClosed(at=2.0, channel_id="k1"))
    after = channels.ChannelWriter(log)
    after.close()
    after.submit(K1, _created("k2"))
    assert isinstance(after.drain()[0].result, NotAppended)


def test_a_fact_that_does_not_land_stops_its_link_and_restores_it_not_live(
    tmp_path: Path,
) -> None:
    """
    The link landed, its close did not: the writer refuses the link's later
    facts, the stop marker lands, and the next start withholds the link.
    Another link is untouched.
    """
    inner = FileChannels(tmp_path)
    other = LinkKey("ops", "k2")
    assert inner.append(K1, _created()) == Appended()
    log = _Failing(inner, K1)
    writer = channels.ChannelWriter(log)
    assert writer.refusal(K1) is None
    writer.submit(K1, LinkClosed(at=2.0, channel_id="k1"))
    writer.submit(other, _created("k2"))
    writer.submit(K1, LinkClosed(at=3.0, channel_id="k1"))

    first, second, later = writer.close()

    assert isinstance(first.result, NotAppended) and first.marker == Appended()
    assert second.result == Appended()
    # Refused even though the log would take it now: the log is missing a fact
    # from its middle.
    assert isinstance(later.result, NotAppended) and "earlier" in later.result.reason
    reason = writer.refusal(K1)
    assert reason is not None and "no space left" in reason
    assert writer.refusal(other) is None
    state = inner.state(K1)
    assert isinstance(state, Stopped) and "no space left" in state.reason
    restored = _restored(inner)
    assert restored[K1].withheld is not None
    assert restored[other].withheld is None


# -- linking a name closes what was left under it ------------------------------------


def _link(log: ChannelLog, made: LinkKey) -> tuple[channels.Outcome, ...]:
    """The operator's link of ``made``, as the writer appends it."""
    writer = channels.ChannelWriter(log)
    writer.submit(made, _created(made.link_id, 5.0))
    return writer.close()


@pytest.mark.skipif(not _unlistable(), reason="root reads any directory")
def test_a_link_withheld_by_a_read_error_is_closed_by_the_next_link_of_its_name(
    tmp_path: Path,
) -> None:
    """
    RV4b's R1: k1's directory cannot be read at one start, so k1 is withheld
    and never reaches the store, and the operator links the name again as k2.
    Once k1 reads again, k1 is closed and k2 alone is live -- not two live
    links under one name, withheld at every start from then on.
    """
    log = FileChannels(tmp_path)
    assert log.append(K1, _created()) == Appended()
    (tmp_path / "brand" / "k1").chmod(0)
    try:
        withheld = _restored(log)[K1].withheld
    finally:
        (tmp_path / "brand" / "k1").chmod(0o700)
    assert withheld is not None and "its directory cannot be listed" in withheld
    k2 = LinkKey("brand", "k2")

    reports = _link(log, k2)

    assert [(r.link, type(r.fact), r.result) for r in reports] == [
        (K1, LinkClosed, Appended()),
        (k2, LinkCreated, Appended()),
    ]
    restored = _restored(log)
    closed, made = restored[K1].channel, restored[k2].channel
    assert restored[K1].withheld is None and closed is not None and not closed.live
    assert restored[k2].withheld is None and made is not None and made.live


def test_a_link_withheld_by_a_newer_kind_gets_its_close_for_the_newer_build(
    tmp_path: Path,
) -> None:
    """
    k1 holds a kind this build does not define, so it is withheld here; the
    operator links the name again. The close lands behind the newer record,
    so a newer build reading k1 finds it closed and k2 the one live link.
    """
    log = FileChannels(tmp_path)
    assert log.append(K1, _created()) == Appended()
    assert log.append(K1, _newer()) == Appended()

    _link(log, LinkKey("brand", "k2"))

    facts = log.read(K1).facts
    assert facts[:2] == (_created(), _newer()) and isinstance(facts[2], LinkClosed)
    assert _restored(log)[LinkKey("brand", "k2")].withheld is None


def test_two_live_links_under_one_name_are_both_closed_by_the_next_link(tmp_path: Path) -> None:
    """
    Two windows each linked the name, so both links are withheld. The next
    link of the name closes both, and after a restart it alone is live. A
    link under another name, and a link closed already, get nothing.
    """
    log = FileChannels(tmp_path)
    k2, k3, gone = (LinkKey("brand", i) for i in ("k2", "k3", "k0"))
    ops = LinkKey("ops", "k9")
    for link, fact in (
        (K1, _created("k1")),
        (k2, _created("k2")),
        (gone, _created("k0")),
        (gone, LinkClosed(at=2.0, channel_id="k0")),
        (ops, _created("k9")),
    ):
        assert log.append(link, fact) == Appended()
    assert _restored(log)[K1].withheld == "2 links under #brand are live at once"

    reports = _link(log, k3)

    assert [r.link for r in reports] == [K1, k2, k3]
    assert all(r.result == Appended() for r in reports)
    assert [r.swept and r.swept.made for r in reports] == [k3, k3, None]
    restored = _restored(log)
    assert {link for link, r in restored.items() if r.channel and r.channel.live} == {k3, ops}
    assert all(r.withheld is None for r in restored.values())
    assert len(log.read(gone).facts) == 2


def test_a_close_that_does_not_land_is_said_and_the_new_link_is_still_made(
    tmp_path: Path,
) -> None:
    """
    k1's close fails: k1's recording stops and its marker makes it not live,
    the failure reaches the log panel as any fact's does, and k2 is linked.
    """
    from pptmstr import app
    from pptmstr.log import LOG

    inner = FileChannels(tmp_path)
    assert inner.append(K1, _created()) == Appended()
    k2 = LinkKey("brand", "k2")

    swept, made = _link(_Failing(inner, K1), k2)

    assert swept.link == K1 and isinstance(swept.fact, LinkClosed)
    assert isinstance(swept.result, NotAppended) and swept.marker == Appended()
    # Said as the sweep's, of an earlier link made when its log says.
    assert swept.swept == Swept(k2, _created().at)
    assert made.link == k2 and made.result == Appended() and made.swept is None
    mark = LOG.snapshot()[1]
    app._read_channel_reports((swept, made))
    entries, revision = LOG.snapshot()
    said = [e.text for e in entries[len(entries) - (revision - mark) :]]
    assert len(said) == 1
    assert "#brand (k1): a LinkClosed did not reach" in said[0]
    assert "no space left" in said[0] and "recording stopped" in said[0]
    restored = _restored(inner)
    assert restored[K1].withheld is not None
    assert restored[k2].withheld is None


def test_links_that_cannot_be_listed_close_nothing_and_the_new_link_is_still_made(
    tmp_path: Path,
) -> None:
    inner = FileChannels(tmp_path)
    assert inner.append(K1, _created()) == Appended()
    k2 = LinkKey("brand", "k2")

    class Unlistable(_Failing):
        def links(self, name: str | None = None) -> tuple[LinkKey, ...] | Unlisted:
            raise OSError("the mount went away")

    unlisted, made = _link(Unlistable(inner, LinkKey("none", "k0")), k2)

    assert isinstance(unlisted.result, NotAppended)
    assert unlisted.link == k2 and unlisted.swept == Swept(k2)
    assert "could not be listed" in unlisted.result.reason
    assert "the mount went away" in unlisted.result.reason
    assert made.link == k2 and made.result == Appended()
    assert inner.read(K1).facts == (_created(),)


def _bytes(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


def _torn(log: FileChannels, directory: Path) -> None:
    assert log.append(K1, _created()) == Appended()
    _write(directory, 1, "not JSON at all")


def _malformed(log: FileChannels, directory: Path) -> None:
    _write(directory, 0, json.dumps({"kind": "link-created", "at": 1.0, "channel_id": "k1"}))


def _gap(log: FileChannels, directory: Path) -> None:
    for fact in (_created(), _newer(), _newer()):
        assert log.append(K1, fact) == Appended()
    (directory / "00000001.json").unlink()


def _stopped(log: FileChannels, directory: Path) -> None:
    assert log.append(K1, _created()) == Appended()
    assert log.append(K1, RecordingStopped(at=2.0, reason="the disk was full")) == Appended()


def _closed(log: FileChannels, directory: Path) -> None:
    assert log.append(K1, _created()) == Appended()
    assert log.append(K1, LinkClosed(at=2.0, channel_id="k1")) == Appended()


def _contested(log: FileChannels, directory: Path) -> None:
    for fact in (_created(), _created(at=2.0)):
        assert log.append(K1, fact) == Appended()


def _stray(log: FileChannels, directory: Path) -> None:
    assert log.append(K1, _created("k7")) == Appended()


@pytest.mark.parametrize(
    "make",
    [_torn, _malformed, _gap, _stopped, _closed, _contested, _stray],
    ids=lambda f: f.__name__,
)
def test_a_relink_leaves_a_log_that_can_never_be_live_byte_for_byte(
    tmp_path: Path, make: Callable[[FileChannels, Path], None]
) -> None:
    """
    RV4c F3 and F5(a). Damage no read gets past, a stop marker, a close, a
    second creation and a fact about another link each rule the link out for
    good, so a close appended to it is a record and nothing more -- at every
    relink of the name. Two relinks leave the log as it was.
    """
    log = FileChannels(tmp_path)
    directory = tmp_path / "brand" / "k1"
    make(log, directory)
    before = _bytes(directory)
    assert channels.owes_close(K1, log.read(K1)) is False

    for again in ("k2", "k3"):
        reports = _link(log, LinkKey("brand", again))
        assert all(r.link != K1 for r in reports)

    assert _bytes(directory) == before


class _Full(_Failing):
    """Every append to one link fails, its stop marker too, until ``fail`` is unset."""

    def append(self, link: LinkKey, fact: channels.Fact) -> Appended | NotAppended:
        if link == self.failing and self.fail:
            return NotAppended("no space left on device")
        return self.inner.append(link, fact)


def _settled(writer: channels.ChannelWriter, count: int) -> list[channels.Outcome]:
    """The writer's next ``count`` outcomes, waited for."""
    got: list[channels.Outcome] = []
    deadline = time.monotonic() + 5.0
    while len(got) < count:
        assert time.monotonic() < deadline, got
        got.extend(writer.drain())
        time.sleep(0.005)
    return got


@pytest.mark.parametrize("marker", ["landed", "lost"])
def test_a_link_this_process_stopped_is_closed_by_a_relink_only_if_it_could_read_live(
    tmp_path: Path, marker: str
) -> None:
    """
    RV4c F5(b). The operator's close of k1 fails and k1 stops being recorded
    in this process; then the name is linked again, by the same writer. When
    the stop marker landed, k1 reads stopped and is left as it is. When the
    marker was lost too -- a full disk -- k1 reads whole and live, so the
    relink closes it, past ``refusal``, once the disk takes it.
    """
    inner = FileChannels(tmp_path)
    assert inner.append(K1, _created()) == Appended()
    log = (_Failing if marker == "landed" else _Full)(inner, K1)
    writer = channels.ChannelWriter(log)
    writer.submit(K1, LinkClosed(at=2.0, channel_id="k1"))
    (failed,) = _settled(writer, 1)
    assert isinstance(failed.result, NotAppended) and writer.refusal(K1) is not None
    assert (failed.marker == Appended()) == (marker == "landed")
    log.fail = False
    before = _bytes(tmp_path / "brand" / "k1")
    k2 = LinkKey("brand", "k2")

    writer.submit(k2, _created("k2", 5.0))
    reports = writer.close()

    restored = _restored(inner)
    assert restored[k2].withheld is None
    if marker == "landed":
        assert [r.link for r in reports] == [k2]
        assert _bytes(tmp_path / "brand" / "k1") == before
        assert restored[K1].withheld is not None
    else:
        assert [(r.link, type(r.fact), r.result) for r in reports] == [
            (K1, LinkClosed, Appended()),
            (k2, LinkCreated, Appended()),
        ]
        closed = restored[K1].channel
        assert restored[K1].withheld is None and closed is not None and not closed.live


@pytest.mark.skipif(not _unlistable(), reason="root reads any file")
def test_a_log_that_cannot_be_read_now_is_closed_for_when_it_can(tmp_path: Path) -> None:
    """
    A record that cannot be opened is not damage: a later read may get past
    it and find the link live, so the relink closes it, and once the record
    reads again the link is closed.
    """
    log = FileChannels(tmp_path)
    assert log.append(K1, _created()) == Appended()
    record = tmp_path / "brand" / "k1" / "00000000.json"
    record.chmod(0)
    try:
        read = log.read(K1)
        assert read.broken is not None and not read.damaged
        assert channels.owes_close(K1, read) is True
        reports = _link(log, LinkKey("brand", "k2"))
    finally:
        record.chmod(0o600)

    assert reports[0].link == K1 and reports[0].result == Appended()
    closed = _restored(log)[K1].channel
    assert closed is not None and not closed.live


@pytest.mark.skipif(not _unlistable(), reason="root lists any directory")
def test_an_unlistable_directory_under_another_name_does_not_stop_the_sweep(
    tmp_path: Path,
) -> None:
    """RV4c F6: the sweep lists its own name's links and no other name's."""
    log = FileChannels(tmp_path)
    for link in (K1, LinkKey("ops", "k9")):
        assert log.append(link, _created(link.link_id)) == Appended()
    (tmp_path / "ops").chmod(0)
    try:
        assert isinstance(log.links(), Unlisted)
        reports = _link(log, LinkKey("brand", "k2"))
    finally:
        (tmp_path / "ops").chmod(0o700)

    assert [(r.link, r.result) for r in reports] == [
        (K1, Appended()),
        (LinkKey("brand", "k2"), Appended()),
    ]


def test_a_link_of_a_newer_shape_is_closed_and_a_log_with_no_link_is_left(
    tmp_path: Path,
) -> None:
    """
    A link of three members is not this build's to restore, and may be a
    newer build's live one: it is closed. A directory with no record yet --
    a creation still being written -- is owed nothing now, and is not
    settled: once its creation lands, the next relink closes it.
    """
    log = FileChannels(tmp_path)
    assert log.append(K1, _created(members=(A, B, C))) == Appended()
    empty = LinkKey("brand", "k0")
    (tmp_path / "brand" / "k0").mkdir()
    writer = channels.ChannelWriter(log)

    writer.submit(LinkKey("brand", "k2"), _created("k2", 5.0))
    first = _settled(writer, 2)
    assert log.append(empty, _created("k0")) == Appended()
    writer.submit(LinkKey("brand", "k3"), _created("k3", 6.0))
    later = writer.close()

    assert [r.link.link_id for r in first] == ["k1", "k2"]
    assert [r.link.link_id for r in later] == ["k0", "k2", "k3"]
    assert all(r.result == Appended() for r in (*first, *later))
    assert isinstance(log.read(K1).facts[-1], LinkClosed)


def test_a_sweep_close_is_never_before_its_links_creation(tmp_path: Path) -> None:
    """RV4c F8: a link made on a clock ahead of this one still closes after it began."""
    log = FileChannels(tmp_path)
    ahead = time.time() + 3600.0
    assert log.append(K1, _created(at=ahead)) == Appended()

    _link(log, LinkKey("brand", "k2"))

    closed = _restored(log)[K1].channel
    assert closed is not None and closed.closed_at is not None
    assert closed.closed_at >= closed.created_at == ahead


def test_a_writer_reads_a_settled_link_once(tmp_path: Path) -> None:
    """
    RV4c F9: the sweep reads every earlier link under the name, so it is
    bounded by not reading again one this writer has seen ruled out for good.
    """
    inner = FileChannels(tmp_path)
    for fact in (_created(), LinkClosed(at=2.0, channel_id="k1")):
        assert inner.append(K1, fact) == Appended()
    reads: list[LinkKey] = []

    class Counting(_Failing):
        def read(self, link: LinkKey) -> Read:
            reads.append(link)
            return self.inner.read(link)

    writer = channels.ChannelWriter(Counting(inner, LinkKey("none", "k0")))
    for again in ("k2", "k3", "k4"):
        writer.submit(LinkKey("brand", again), _created(again, 5.0))
    writer.close()

    # k2 is read once, by the sweep that closes it; k3 once, by k4's.
    assert sorted(reads) == [K1, LinkKey("brand", "k2"), LinkKey("brand", "k3")]


# -- the startup read's remaining edges ---------------------------------------------


def test_one_link_id_under_two_names_is_withheld_under_both(tmp_path: Path) -> None:
    """A directory copied by hand: the store would keep one of the two silently."""
    log = FileChannels(tmp_path)
    copy = LinkKey("ops", "k1")
    for link in (K1, copy):
        assert log.append(link, _created()) == Appended()

    restored = _restored(log)

    assert restored[K1].withheld == "its id is also a link's under #ops"
    assert restored[copy].withheld == "its id is also a link's under #brand"


def test_a_link_withheld_for_its_id_does_not_count_against_its_names_live_link(
    tmp_path: Path,
) -> None:
    """RV4c F7: only links that could be live are counted as live under a name."""
    log = FileChannels(tmp_path)
    sibling = LinkKey("brand", "k2")
    for link in (K1, LinkKey("ops", "k1"), sibling):
        assert log.append(link, _created(link.link_id)) == Appended()

    restored = _restored(log)

    assert restored[K1].withheld == "its id is also a link's under #ops"
    assert restored[sibling].withheld is None
