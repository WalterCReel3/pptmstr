"""
Requests between sessions: writing records, reading them back, deriving their state.

Driven against a real directory rather than a fake filesystem, because the properties
that matter most are properties of the write primitive -- that a second writer cannot
replace a record, that a number is spent only by a whole record, and that a write
lands in the writer's directory and nowhere else.

The invariants are checked over seeded random runs rather than over examples: every
write leaves every other party's files byte-identical, no file changes once it
exists, and the derived state is the same for the same records however they were
interleaved on the way to disk and in whatever order they come back.
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import threading
import tracemalloc
from collections.abc import Callable
from dataclasses import fields, replace
from pathlib import Path

import pytest

from pptmstr import requests
from pptmstr.model import Basis
from pptmstr.requests import (
    Delivery,
    ProblemKind,
    Quote,
    RecordId,
    Refusal,
    RequestRef,
    State,
)

OPERATORS = frozenset({"operator"})


@pytest.fixture(autouse=True)
def flushes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """
    The descriptors flushed to the device, in place of flushing them.

    A real flush is several milliseconds and the random runs below make thousands
    of writes. What is worth pinning is that it happens before the record has its
    name, which ``test_a_record_is_on_the_device_before_it_has_a_name`` does.
    """
    flushed: list[int] = []
    monkeypatch.setattr(os, "fsync", flushed.append)
    return flushed


def hold_nothing(record: requests.Request | requests.Answer) -> bool:
    return False


def hold_from(*writers: str) -> Callable[[requests.Request | requests.Answer], bool]:
    """
    A rule that holds what the named parties wrote.

    Keyed on the directory a record was found in, which is the one thing about its
    sender that a record's writer does not choose. A rule reading ``sender_contained``
    would be one the sender answers for itself.
    """

    def rule(record: requests.Request | requests.Answer) -> bool:
        return record.id.party in writers

    return rule


HOLD_SOME = hold_from("alpha", "gamma")


def phantom(asker: str, number: int) -> requests.Request:
    """
    A request that is on no disk, for a caller holding a reference to nothing.
    """
    return requests.Request(asker, number, "beta", "s", "b", Basis.READ, "", 1.0, None, None)


def rewrite(root: Path, record: requests.Record, **changed: object) -> None:
    """
    Overwrite a record's own file with some of its fields changed, as its writer
    can at any time: the file is in the writer's directory.
    """
    path = root / record.id.party / f"{record.id.name}.json"
    path.write_text(json.dumps({**requests._encode(record), **changed}))


def ask(
    root: Path,
    asker: str = "alpha",
    to: str = "beta",
    *,
    now: float = 100.0,
    contained: bool | None = False,
    body: str = "does the cell round up?",
) -> requests.Request:
    made = requests.post_request(
        root,
        asker,
        to=to,
        subject="the cell",
        body=body,
        basis=Basis.READ,
        blocks="the diff view",
        now=now,
        sender_policy="strict",
        sender_contained=contained,
    )
    assert isinstance(made, requests.Request)
    return made


def answer(
    root: Path,
    answerer: str,
    request: requests.Request,
    *,
    now: float = 200.0,
    contained: bool | None = False,
    body: str = "it rounds down",
) -> requests.Answer | Refusal:
    return requests.post_answer(
        root,
        answerer,
        request,
        body=body,
        basis=Basis.RUN,
        now=now,
        sender_policy="strict",
        sender_contained=contained,
    )


def answered(
    root: Path,
    answerer: str,
    request: requests.Request,
    *,
    now: float = 200.0,
    contained: bool | None = False,
    body: str = "it rounds down",
) -> requests.Answer:
    made = answer(root, answerer, request, now=now, contained=contained, body=body)
    assert isinstance(made, requests.Answer)
    return made


def view(
    root: Path, holds: Callable[[requests.Request | requests.Answer], bool] = hold_nothing
) -> requests.View:
    reading = requests.read(root)
    return requests.derive(reading.records, holds=holds, operators=OPERATORS, unread=reading.unread)


def tree(root: Path) -> dict[str, bytes]:
    """
    Every file under the root, temp files included, by relative path.
    """
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def force(root: Path, record: requests.Record) -> None:
    """
    Put a record on disk in its writer's directory without any of the checks.

    Stands in for a file that did not come through this module's write functions,
    which is the case ``derive`` re-checks every rule for.
    """
    series = "request" if isinstance(record, requests.Request) else "entry"
    directory = root / record.id.party
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{record.id.name}.json").write_text(json.dumps(requests._encode(record)))
    assert record.id.name.startswith(series)


# -- addresses --------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    ["", ".", "..", ".hidden", "a/b", "/abs", "a\\b", "nul\0byte", "x" * 256, "é" * 128, "\ud800"],
)
def test_an_address_that_is_not_one_directory_name_is_refused(tmp_path: Path, address: str) -> None:
    before = tree(tmp_path)

    as_asker = requests.post_request(
        tmp_path,
        address,
        to="beta",
        subject="s",
        body="b",
        basis=Basis.READ,
        blocks="",
        now=1.0,
        sender_policy=None,
        sender_contained=None,
    )
    as_addressee = requests.post_request(
        tmp_path,
        "alpha",
        to=address,
        subject="s",
        body="b",
        basis=Basis.READ,
        blocks="",
        now=1.0,
        sender_policy=None,
        sender_contained=None,
    )

    assert as_asker is Refusal.UNSAFE_ADDRESS
    assert as_addressee is Refusal.UNSAFE_ADDRESS
    assert tree(tmp_path) == before
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "address",
    [
        "3f0c9a52-7a1e-4c1b-9d8e-0c5a4f6b7a10",
        "imgui-adv-text",
        "a name with spaces",
        "x" * 255,
        "é" * 127,
        "trailing.dot.",
    ],
)
def test_an_address_is_otherwise_opaque(tmp_path: Path, address: str) -> None:
    """
    A session id and a directory-style name are both addresses. Nothing about
    either form is assumed.
    """
    made = ask(tmp_path, asker=address, to=address)

    assert made.asker == address
    assert (tmp_path / address / "request-000001.json").is_file()
    assert view(tmp_path).threads[0].request == made


@pytest.mark.parametrize("basis", list(Basis))
def test_a_basis_is_stored_as_the_board_spells_it(tmp_path: Path, basis: Basis) -> None:
    """
    One spelling of run, read and relayed across the board and this channel: the
    file holds ``model.Basis``'s own value and reads back as the member.
    """
    made = requests.post_request(
        tmp_path,
        "alpha",
        to="beta",
        subject="s",
        body="b",
        basis=basis,
        blocks="",
        now=1.0,
        sender_policy=None,
        sender_contained=None,
    )
    assert isinstance(made, requests.Request)
    reply = requests.post_answer(
        tmp_path,
        "beta",
        made,
        body="b",
        basis=basis,
        now=1.0,
        sender_policy=None,
        sender_contained=None,
    )
    assert isinstance(reply, requests.Answer)

    on_disk = json.loads((tmp_path / "alpha" / "request-000001.json").read_text())

    assert on_disk["basis"] == basis.value
    assert requests.read(tmp_path) == requests.Reading((made, reply), (), frozenset())
    assert made.basis is basis and reply.basis is basis


# -- what a record carries --------------------------------------------------------


def test_a_request_reads_back_exactly_as_it_was_written(tmp_path: Path) -> None:
    """
    Including the operator's words. The quote's text has leading and trailing
    whitespace, a blank line and a lone surrogate, each of which a tidying step or
    a UTF-8 write would lose.
    """
    quote = Quote(
        text="  Let's target Level 1 ...\n\n\ttabbed \ud83d ", source="operator, 2026-10-11"
    )
    made = requests.post_request(
        tmp_path,
        "alpha",
        to="beta",
        subject=" padded subject ",
        body="\nbody with a trailing newline\n",
        basis=Basis.RELAYED,
        blocks="swap-core",
        now=1760000000.25,
        sender_policy="autonomous",
        sender_contained=True,
        quotes=(quote, Quote("", "")),
    )

    (read_back,) = requests.read(tmp_path).records

    assert read_back == made
    assert isinstance(read_back, requests.Request)
    assert read_back.quotes[0].text == quote.text
    assert (read_back.sender_policy, read_back.sender_contained) == ("autonomous", True)
    assert read_back.ref == RequestRef("alpha", 1)


@pytest.mark.parametrize("contained", [True, False, None])
def test_what_the_sender_did_not_know_is_not_stored_as_an_answer(
    tmp_path: Path, contained: bool | None
) -> None:
    """
    None, True and False are three stored values. A hold rule that treats "the
    handler did not say" as contained can only do so if None survives the disk.
    """
    made = requests.post_request(
        tmp_path,
        "alpha",
        to="beta",
        subject="s",
        body="b",
        basis=Basis.READ,
        blocks="",
        now=1.0,
        sender_policy=None,
        sender_contained=contained,
    )
    assert isinstance(made, requests.Request)
    reply = answered(tmp_path, "beta", made, contained=contained)

    stored = {r.id: r for r in requests.read(tmp_path).records}

    assert stored[made.id].sender_contained is contained  # type: ignore[union-attr]
    assert stored[made.id].sender_policy is None  # type: ignore[union-attr]
    assert stored[reply.id].sender_contained is contained  # type: ignore[union-attr]


def test_every_kind_of_record_reads_back_equal(tmp_path: Path) -> None:
    asked = ask(tmp_path)
    reply = answered(tmp_path, "beta", asked)
    receipt = requests.acknowledge(tmp_path, "alpha", reply, now=300.0)
    let_through = requests.release(tmp_path, "operator", asked, now=150.0)
    close = requests.close_request(tmp_path, "alpha", asked.ref, now=400.0)

    assert set(requests.read(tmp_path).records) == {asked, reply, receipt, let_through, close}
    assert requests.read(tmp_path).problems == ()


# -- where a record is written ----------------------------------------------------


def test_each_record_lives_with_the_party_that_wrote_it(tmp_path: Path) -> None:
    """
    The answer is in the answerer's directory and names the asker; the receipt is
    in the reader's; the close is in the asker's; the release is in the operator's.
    """
    asked = ask(tmp_path, "alpha", "beta")
    reply = answered(tmp_path, "beta", asked)
    requests.acknowledge(tmp_path, "beta", asked, now=1.0)
    requests.acknowledge(tmp_path, "alpha", reply, now=1.0)
    requests.release(tmp_path, "operator", asked, now=1.0)
    requests.close_request(tmp_path, "alpha", asked.ref, now=1.0)

    assert sorted(tree(tmp_path)) == [
        "alpha/entry-000001.json",
        "alpha/entry-000002.json",
        "alpha/request-000001.json",
        "beta/entry-000001.json",
        "beta/entry-000002.json",
        "operator/entry-000001.json",
    ]
    assert reply.ref == RequestRef("alpha", 1)


def test_a_party_that_has_written_nothing_can_be_asked(tmp_path: Path) -> None:
    asked = ask(tmp_path, "alpha", "nobody-yet")

    assert not (tmp_path / "nobody-yet").exists()
    assert [i.record for i in requests.pending_for(view(tmp_path), "nobody-yet")] == [asked]


# -- numbering --------------------------------------------------------------------


def test_requests_are_numbered_from_one_per_asker(tmp_path: Path) -> None:
    """
    An asker's numbers run 1, 2, 3 whatever else it wrote in between, and another
    asker's start again -- which is why a bare number is not a reference.
    """
    first = ask(tmp_path, "alpha", "beta")
    theirs = ask(tmp_path, "beta", "alpha")
    answered(tmp_path, "alpha", theirs)
    requests.acknowledge(tmp_path, "alpha", theirs, now=1.0)
    second = ask(tmp_path, "alpha", "beta")

    assert (first.number, second.number, theirs.number) == (1, 2, 1)
    assert first.ref != theirs.ref
    assert requests.next_number(tmp_path, "alpha") == 3
    assert requests.next_number(tmp_path, "nobody-yet") == 1


@pytest.mark.parametrize("content", [b"", b"{not json", b'{"kind": "request"}', b"\xff\xfe"])
def test_a_number_whose_record_cannot_be_read_is_still_spent(
    tmp_path: Path, content: bytes
) -> None:
    ask(tmp_path)
    ask(tmp_path)
    (tmp_path / "alpha" / "request-000002.json").write_bytes(content)

    third = ask(tmp_path)

    assert third.number == 3


def test_a_number_is_spent_only_by_a_whole_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A writer that fails between writing the content and taking the name leaves no
    numbered file, so there is nothing half-written to read and no number gone.
    """
    ask(tmp_path)

    def dies(src: str, dst: str) -> None:
        raise OSError("the process went away here")

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", dies)
        with pytest.raises(OSError, match="went away"):
            ask(tmp_path)

    assert sorted(tree(tmp_path)) == ["alpha/request-000001.json"]
    assert ask(tmp_path).number == 2


def test_a_write_abandoned_mid_flight_is_not_read_and_takes_no_number(tmp_path: Path) -> None:
    """
    What a killed writer leaves is a temp file. A reader passing it must neither
    trip over it nor report it, since any read overlapping a live write sees one.
    """
    first = ask(tmp_path)
    (tmp_path / "alpha" / ".writing-abc123.json").write_text('{"kind": "request", "asker": "al')

    reading = requests.read(tmp_path)

    assert reading.records == (first,)
    assert reading.problems == ()
    assert ask(tmp_path).number == 2


def test_a_read_that_overlaps_a_write_sees_no_record_and_no_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The read happens at the last moment before the record has its name, with the
    content already complete in the writer's own temp file.
    """
    first = ask(tmp_path)
    link = os.link
    seen: list[requests.Reading] = []

    def read_then_link(src: str, dst: Path) -> None:
        assert Path(src).read_text().endswith("}\n")
        seen.append(requests.read(tmp_path))
        link(src, dst)

    monkeypatch.setattr(os, "link", read_then_link)
    second = ask(tmp_path)

    assert seen == [requests.Reading((first,), (), frozenset())]
    assert requests.read(tmp_path) == requests.Reading((first, second), (), frozenset())


def test_a_record_is_on_the_device_before_it_has_a_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flushes: list[int]
) -> None:
    """
    Named first and flushed second, a power loss between the two leaves a number
    spent on a short file.
    """
    link = os.link
    flushed_at_link: list[int] = []

    def counting(src: str, dst: Path) -> None:
        flushed_at_link.append(len(flushes))
        link(src, dst)

    monkeypatch.setattr(os, "link", counting)
    asked = ask(tmp_path)
    answered(tmp_path, "beta", asked)

    assert flushed_at_link == [1, 2]


def test_no_temp_file_outlives_a_write(tmp_path: Path) -> None:
    asked = ask(tmp_path)
    answered(tmp_path, "beta", asked)

    assert [name for name in tree(tmp_path) if "/." in name] == []


def test_a_second_writer_with_a_stale_listing_takes_the_next_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Two processes holding one address both conclude the next number is 1. The
    second must end up at 2 with the first's record untouched; a rename into place
    would leave one file holding the second writer's request and nothing raised.
    """
    monkeypatch.setattr(requests, "_next_serial", lambda directory, series: 1)

    first = ask(tmp_path, body="from the first window")
    kept = (tmp_path / "alpha" / "request-000001.json").read_bytes()
    second = ask(tmp_path, body="from the second window")
    third = ask(tmp_path, body="and a third")

    assert (first.number, second.number, third.number) == (1, 2, 3)
    assert (tmp_path / "alpha" / "request-000001.json").read_bytes() == kept
    assert requests.read(tmp_path).records == (first, second, third)


def test_writers_racing_on_one_address_lose_nothing(tmp_path: Path) -> None:
    writers, each = 8, 25
    made: list[requests.Request] = []
    start = threading.Barrier(writers)

    def run(index: int) -> None:
        start.wait()
        for n in range(each):
            made.append(ask(tmp_path, body=f"{index}/{n}"))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    on_disk = requests.read(tmp_path)

    assert sorted(r.number for r in made) == list(range(1, writers * each + 1))
    assert on_disk.problems == ()
    assert sorted(on_disk.records, key=lambda r: r.id) == sorted(made, key=lambda r: r.id)
    assert {r.body for r in made} == {f"{i}/{n}" for i in range(writers) for n in range(each)}


def test_a_series_past_its_padding_still_counts_by_the_integer(tmp_path: Path) -> None:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "request-999999.json").write_text("{}")

    made = ask(tmp_path)
    after = ask(tmp_path)

    assert (made.number, after.number) == (1000000, 1000001)
    assert (tmp_path / "alpha" / "request-1000000.json").is_file()
    assert made in requests.read(tmp_path).records


# -- refusals ---------------------------------------------------------------------


def test_only_the_asker_closes_a_request(tmp_path: Path) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    before = tree(tmp_path)

    by_addressee = requests.close_request(tmp_path, "beta", asked.ref, now=1.0)
    by_stranger = requests.close_request(tmp_path, "gamma", asked.ref, now=1.0)

    assert by_addressee is Refusal.NOT_YOURS_TO_CLOSE
    assert by_stranger is Refusal.NOT_YOURS_TO_CLOSE
    assert tree(tmp_path) == before
    assert view(tmp_path).threads[0].state is State.OPEN

    assert isinstance(requests.close_request(tmp_path, "alpha", asked.ref, now=1.0), requests.Close)
    assert view(tmp_path).threads[0].state is State.CLOSED


def test_only_the_addressee_answers_a_request(tmp_path: Path) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    before = tree(tmp_path)

    assert answer(tmp_path, "gamma", asked) is Refusal.NOT_ADDRESSED_TO_YOU
    assert answer(tmp_path, "alpha", asked) is Refusal.NOT_ADDRESSED_TO_YOU
    assert tree(tmp_path) == before


def test_an_answer_to_a_number_that_was_never_issued_is_refused_as_that(tmp_path: Path) -> None:
    """
    Told apart from "not addressed to you": one is a wrong number, the other a
    request that exists and is somebody else's to answer.
    """
    ask(tmp_path, "alpha", "beta")
    before = tree(tmp_path)

    assert answer(tmp_path, "beta", phantom("alpha", 2)) is Refusal.NO_SUCH_REQUEST
    assert answer(tmp_path, "beta", phantom("alhpa", 1)) is Refusal.NO_SUCH_REQUEST
    assert answer(tmp_path, "beta", phantom("../alpha", 1)) is Refusal.NO_SUCH_REQUEST
    assert requests.close_request(tmp_path, "alpha", RequestRef("alpha", 2), now=1.0) is (
        Refusal.NO_SUCH_REQUEST
    )
    assert tree(tmp_path) == before


def test_a_closed_request_takes_no_further_answer_and_no_second_close(tmp_path: Path) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    requests.close_request(tmp_path, "alpha", asked.ref, now=1.0)
    before = tree(tmp_path)

    assert answer(tmp_path, "beta", asked) is Refusal.ALREADY_CLOSED
    assert requests.close_request(tmp_path, "alpha", asked.ref, now=2.0) is Refusal.ALREADY_CLOSED
    assert tree(tmp_path) == before


def test_a_receipt_or_a_release_for_nothing_is_refused(tmp_path: Path) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    close = requests.close_request(tmp_path, "alpha", asked.ref, now=1.0)
    assert isinstance(close, requests.Close)
    before = tree(tmp_path)
    missing = phantom("alpha", 9)

    assert requests.acknowledge(tmp_path, "beta", missing, now=1.0) is Refusal.NO_SUCH_RECORD
    assert requests.release(tmp_path, "operator", missing, now=1.0) is Refusal.NO_SUCH_RECORD
    # A close is a record on disk, and is not something a party is shown or held from.
    not_shown = requests.acknowledge(tmp_path, "beta", close, now=1.0)  # type: ignore[arg-type]
    assert not_shown is Refusal.NO_SUCH_RECORD
    assert requests.acknowledge(tmp_path, "../beta", asked, now=1.0) is Refusal.UNSAFE_ADDRESS
    assert requests.release(tmp_path, "a/b", asked, now=1.0) is Refusal.UNSAFE_ADDRESS
    assert tree(tmp_path) == before


def test_the_refusal_rules_are_checkable_without_a_filesystem() -> None:
    asked = requests.Request("alpha", 1, "beta", "s", "b", Basis.READ, "", 1.0, None, None)
    closed = requests.Request("alpha", 2, "beta", "s", "b", Basis.READ, "", 1.0, None, None)
    records: tuple[requests.Record, ...] = (
        asked,
        closed,
        requests.Close("alpha", 1, closed.ref, 2.0),
        # A close its writer had no standing to write does not close anything.
        requests.Close("beta", 1, asked.ref, 2.0),
    )

    assert requests.answer_refusal(records, "beta", asked) is None
    assert requests.answer_refusal(records, "gamma", asked) is Refusal.NOT_ADDRESSED_TO_YOU
    assert requests.answer_refusal(records, "beta", closed) is Refusal.ALREADY_CLOSED
    assert requests.answer_refusal(records, "beta", phantom("alpha", 3)) is Refusal.NO_SUCH_REQUEST
    # Shown one text and answering another: told so ahead of everything else, the
    # addressee included, because the addressee is one of the things that may differ.
    reworded = replace(asked, body="something else", to="gamma")
    assert requests.answer_refusal(records, "beta", reworded) is Refusal.CHANGED
    assert requests.answer_refusal(records, "gamma", reworded) is Refusal.CHANGED
    assert requests.close_refusal(records, "alpha", asked.ref) is None
    assert requests.close_refusal(records, "beta", asked.ref) is Refusal.NOT_YOURS_TO_CLOSE
    assert requests.close_refusal(records, "alpha", closed.ref) is Refusal.ALREADY_CLOSED
    assert requests.close_refusal(records, "alpha", RequestRef("alpha", 3)) is (
        Refusal.NO_SUCH_REQUEST
    )


# -- derivation -------------------------------------------------------------------


def test_a_request_is_open_then_answered_then_closed(tmp_path: Path) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    assert view(tmp_path).thread(asked.ref).state is State.OPEN  # type: ignore[union-attr]

    reply = answered(tmp_path, "beta", asked)
    assert view(tmp_path).thread(asked.ref).state is State.ANSWERED  # type: ignore[union-attr]

    close = requests.close_request(tmp_path, "alpha", asked.ref, now=1.0)
    thread = view(tmp_path).thread(asked.ref)

    assert thread is not None
    assert thread.state is State.CLOSED
    assert [i.record for i in thread.items] == [asked, reply]
    assert thread.close == close
    assert view(tmp_path).thread(RequestRef("alpha", 2)) is None


def test_a_request_closed_unanswered_is_closed(tmp_path: Path) -> None:
    asked = ask(tmp_path)
    requests.close_request(tmp_path, "alpha", asked.ref, now=1.0)

    thread = view(tmp_path).threads[0]

    assert (thread.state, len(thread.items)) == (State.CLOSED, 1)


def test_a_thread_is_ordered_by_the_answerers_numbering_and_not_by_a_clock(tmp_path: Path) -> None:
    """
    The answerer's clock steps backwards between its answers, and it writes to
    another request in between. The thread is still in the order it was written.
    """
    asked = ask(tmp_path, "alpha", "beta", now=5000.0)
    other = ask(tmp_path, "alpha", "beta", now=5000.0)
    first = answered(tmp_path, "beta", asked, now=900.0)
    elsewhere = answered(tmp_path, "beta", other, now=800.0)
    second = answered(tmp_path, "beta", asked, now=700.0)
    third = answered(tmp_path, "beta", asked, now=100.0)

    threads = view(tmp_path).threads

    assert [i.record for i in threads[0].items] == [asked, first, second, third]
    assert [i.record for i in threads[1].items] == [other, elsewhere]


def test_threads_come_back_by_asker_and_number(tmp_path: Path) -> None:
    for asker in ("gamma", "alpha", "beta"):
        for _ in range(11):
            ask(tmp_path, asker, "delta")

    refs = [t.ref for t in view(tmp_path).threads]

    assert refs == [RequestRef(a, n) for a in ("alpha", "beta", "gamma") for n in range(1, 12)]


def test_what_is_pending_is_what_is_addressed_to_a_party_and_not_yet_shown(tmp_path: Path) -> None:
    to_beta = ask(tmp_path, "alpha", "beta")
    to_gamma = ask(tmp_path, "alpha", "gamma")
    reply = answered(tmp_path, "beta", to_beta)

    def pending(party: str) -> list[requests.Record]:
        return [i.record for i in requests.pending_for(view(tmp_path), party)]

    assert pending("beta") == [to_beta]
    assert pending("gamma") == [to_gamma]
    assert pending("alpha") == [reply]

    requests.acknowledge(tmp_path, "beta", to_beta, now=1.0)
    assert pending("beta") == []
    assert pending("gamma") == [to_gamma]
    assert pending("alpha") == [reply]

    # Somebody else having been shown the answer does not make the asker shown it.
    requests.acknowledge(tmp_path, "gamma", reply, now=1.0)
    assert pending("alpha") == [reply]

    requests.acknowledge(tmp_path, "alpha", reply, now=1.0)
    assert pending("alpha") == []
    assert view(tmp_path).thread(to_beta.ref).items[1].shown_to == (  # type: ignore[union-attr]
        "alpha",
        "gamma",
    )


def test_a_second_acknowledgement_writes_nothing(tmp_path: Path) -> None:
    asked = ask(tmp_path)
    first = requests.acknowledge(tmp_path, "beta", asked, now=1.0)
    before = tree(tmp_path)

    again = requests.acknowledge(tmp_path, "beta", asked, now=2.0)
    let_through = requests.release(tmp_path, "operator", asked, now=3.0)
    after_release = tree(tmp_path)
    released_again = requests.release(tmp_path, "operator", asked, now=4.0)

    assert again == first
    assert released_again == let_through
    assert len(after_release) == len(before) + 1
    assert tree(tmp_path) == after_release


def test_nothing_is_pending_on_a_closed_request(tmp_path: Path) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    answered(tmp_path, "beta", asked)
    requests.close_request(tmp_path, "alpha", asked.ref, now=1.0)

    derived = view(tmp_path, hold_from("alpha", "beta"))

    assert requests.pending_for(derived, "beta") == ()
    assert requests.pending_for(derived, "alpha") == ()
    assert requests.held_for(derived, "beta") == ()
    assert requests.held_for(derived, "alpha") == ()


# -- holding ----------------------------------------------------------------------


def test_a_held_request_is_not_pending_until_an_operator_releases_it(tmp_path: Path) -> None:
    """
    The same files under two rules. Which rule the channel runs under is not this
    module's to say, so the test is that each gives its own answer.
    """
    asked = ask(tmp_path, "alpha", "beta")

    held = view(tmp_path, hold_from("alpha"))
    assert held.threads[0].items[0].delivery is Delivery.HELD
    assert requests.pending_for(held, "beta") == ()
    assert [i.record for i in requests.held_for(held, "beta")] == [asked]
    assert requests.held_for(held, "alpha") == ()

    free = view(tmp_path, hold_nothing)
    assert free.threads[0].items[0].delivery is Delivery.DELIVERED
    assert [i.record for i in requests.pending_for(free, "beta")] == [asked]
    assert requests.held_for(free, "beta") == ()

    requests.release(tmp_path, "operator", asked, now=1.0)

    released = view(tmp_path, hold_from("alpha"))
    assert released.threads[0].items[0].delivery is Delivery.RELEASED
    assert [i.record for i in requests.pending_for(released, "beta")] == [asked]
    assert requests.held_for(released, "beta") == ()
    # A release of something no rule holds is not what delivered it.
    assert view(tmp_path, hold_nothing).threads[0].items[0].delivery is Delivery.DELIVERED


def test_an_answer_can_be_held_on_its_way_back(tmp_path: Path) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    reply = answered(tmp_path, "beta", asked)

    derived = view(tmp_path, hold_from("beta"))

    assert [i.delivery for i in derived.threads[0].items] == [Delivery.DELIVERED, Delivery.HELD]
    assert derived.threads[0].state is State.ANSWERED
    assert requests.pending_for(derived, "alpha") == ()
    assert [i.record for i in requests.held_for(derived, "alpha")] == [reply]

    requests.release(tmp_path, "operator", reply, now=1.0)

    let_through = view(tmp_path, hold_from("beta"))
    assert let_through.threads[0].items[1].delivery is Delivery.RELEASED
    assert [i.record for i in requests.pending_for(let_through, "alpha")] == [reply]


def test_a_release_by_a_party_releases_nothing(tmp_path: Path) -> None:
    """
    The asker writes a release for its own held request, in its own directory.
    The record is well formed; what it lacks is a writer the caller named.
    """
    asked = ask(tmp_path, "alpha", "beta")
    forged = requests.release(tmp_path, "alpha", asked, now=1.0)
    assert isinstance(forged, requests.Release)

    derived = view(tmp_path, hold_from("alpha"))

    assert derived.threads[0].items[0].delivery is Delivery.HELD
    assert requests.pending_for(derived, "beta") == ()
    assert [(p.source, p.kind) for p in derived.problems] == [
        ("alpha/entry-000001.json", ProblemKind.UNENTITLED)
    ]


# -- a record its writer rewrote --------------------------------------------------


def stale(derived: requests.View) -> list[str]:
    return [p.source for p in derived.problems if p.kind is ProblemKind.STALE]


def test_a_request_rewritten_by_its_asker_keeps_nothing_done_to_the_one_before(
    tmp_path: Path,
) -> None:
    """
    The operator released one text, the addressee was shown it and answered it, and
    the asker then put another under the same number. None of the three acts is on
    the new text: it is held, unshown and unanswered, and each act is reported.
    """
    holds = hold_from("alpha")
    asked = ask(tmp_path, "alpha", "beta", body="is it safe to keep the staging DB?")
    requests.release(tmp_path, "operator", asked, now=1.0)
    requests.acknowledge(tmp_path, "beta", asked, now=2.0)
    reply = answered(tmp_path, "beta", asked, body="yes")
    requests.acknowledge(tmp_path, "alpha", reply, now=3.0)
    before = view(tmp_path, holds).threads[0]
    assert (before.state, before.items[0].delivery, before.items[0].shown_to) == (
        State.ANSWERED,
        Delivery.RELEASED,
        ("beta",),
    )

    rewrite(tmp_path, asked, body="may I drop the production DB?")

    derived = view(tmp_path, holds)
    (thread,) = derived.threads
    (item,) = thread.items
    assert item.record == replace(asked, body="may I drop the production DB?")
    assert (thread.state, item.delivery, item.shown_to) == (State.OPEN, Delivery.HELD, ())
    assert [i.record for i in requests.held_for(derived, "beta")] == [item.record]
    assert requests.pending_for(derived, "beta") == ()
    assert requests.pending_for(derived, "alpha") == ()
    assert [(p.source, p.kind) for p in derived.problems] == [
        # A receipt for an answer that is itself no longer in the thread.
        ("alpha/entry-000001.json", ProblemKind.ORPHAN),
        ("beta/entry-000001.json", ProblemKind.STALE),
        ("beta/entry-000002.json", ProblemKind.STALE),
        ("operator/entry-000001.json", ProblemKind.STALE),
    ]
    # Under a rule that holds nothing it has arrived, and the addressee is owed it.
    assert [i.record for i in requests.pending_for(view(tmp_path), "beta")] == [item.record]


def test_an_answer_rewritten_by_its_answerer_is_held_and_unshown_again(tmp_path: Path) -> None:
    holds = hold_from("beta")
    asked = ask(tmp_path, "alpha", "beta")
    reply = answered(tmp_path, "beta", asked, body="no")
    requests.release(tmp_path, "operator", reply, now=1.0)
    requests.acknowledge(tmp_path, "alpha", reply, now=2.0)
    assert requests.pending_for(view(tmp_path, holds), "alpha") == ()

    rewrite(tmp_path, reply, body="yes, drop it")

    derived = view(tmp_path, holds)
    item = derived.threads[0].items[1]
    assert item.record == replace(reply, body="yes, drop it")
    assert (item.delivery, item.shown_to) == (Delivery.HELD, ())
    assert [i.record for i in requests.held_for(derived, "alpha")] == [item.record]
    assert stale(derived) == ["alpha/entry-000001.json", "operator/entry-000001.json"]


def test_an_answer_to_a_request_since_readdressed_is_stale_and_not_the_answerers_fault(
    tmp_path: Path,
) -> None:
    """
    The asker moves its request to somebody else after it was answered. The answer
    was written by the addressee of the request it answered; what changed is the
    request, and the report says so.
    """
    asked = ask(tmp_path, "alpha", "beta")
    answered(tmp_path, "beta", asked)

    rewrite(tmp_path, asked, to="gamma")

    derived = view(tmp_path)
    assert [(p.source, p.kind) for p in derived.problems] == [
        ("beta/entry-000001.json", ProblemKind.STALE)
    ]
    assert derived.threads[0].state is State.OPEN
    assert [i.record.id for i in requests.pending_for(derived, "gamma")] == [asked.id]


def test_a_number_issued_again_does_not_inherit_what_was_done_to_its_first_holder(
    tmp_path: Path,
) -> None:
    asked = ask(tmp_path, "alpha", "beta", body="the first holder of number 1")
    requests.release(tmp_path, "operator", asked, now=1.0)
    requests.acknowledge(tmp_path, "beta", asked, now=2.0)
    answered(tmp_path, "beta", asked)
    (tmp_path / "alpha" / "request-000001.json").unlink()

    again = ask(tmp_path, "alpha", "beta", body="the second")

    derived = view(tmp_path, hold_from("alpha"))
    (item,) = derived.threads[0].items
    assert again.ref == asked.ref
    assert (derived.threads[0].state, item.delivery, item.shown_to) == (
        State.OPEN,
        Delivery.HELD,
        (),
    )
    assert len(stale(derived)) == 3


def test_what_a_sender_says_of_itself_is_part_of_what_was_released(tmp_path: Path) -> None:
    """
    The sender's own account of its gate is in the record and the sender can change
    it. Under a rule keyed on who wrote the record that moves nothing by itself --
    and it is a rewrite like any other, so the release of the old text is spent.
    """
    asked = ask(tmp_path, "alpha", "beta", contained=True)
    requests.release(tmp_path, "operator", asked, now=1.0)

    rewrite(tmp_path, asked, sender_contained=False, sender_policy="strict")

    derived = view(tmp_path, hold_from("alpha"))
    assert derived.threads[0].items[0].delivery is Delivery.HELD
    assert stale(derived) == ["operator/entry-000001.json"]


def test_an_act_on_a_record_as_it_was_shown_is_refused_once_the_record_has_changed(
    tmp_path: Path,
) -> None:
    """
    The caller hands over what it saw. Looked up again by name at the write, the
    act would land on whatever is there by then.
    """
    asked = ask(tmp_path, "alpha", "beta")
    reply = answered(tmp_path, "beta", asked)
    rewrite(tmp_path, asked, body="reworded after it was read")
    rewrite(tmp_path, reply, answers="0" * 64)
    before = tree(tmp_path)

    assert answer(tmp_path, "beta", asked) is Refusal.CHANGED
    assert requests.acknowledge(tmp_path, "beta", asked, now=1.0) is Refusal.CHANGED
    assert requests.release(tmp_path, "operator", asked, now=1.0) is Refusal.CHANGED
    # An answer that is no longer to the request as it stands is not a record
    # anybody can be shown, whichever version of it the caller holds.
    assert requests.acknowledge(tmp_path, "alpha", reply, now=1.0) is Refusal.NO_SUCH_RECORD
    assert tree(tmp_path) == before

    current = requests.read(tmp_path).records[0]
    assert isinstance(current, requests.Request)
    assert isinstance(answer(tmp_path, "beta", current), requests.Answer)
    assert isinstance(requests.acknowledge(tmp_path, "beta", current, now=1.0), requests.Receipt)


def test_a_receipt_for_what_a_record_used_to_say_is_not_a_receipt_for_what_it_says(
    tmp_path: Path,
) -> None:
    """
    Acknowledging the new text writes a second receipt, and releasing it a second
    release. Returning the one on disk, as a repeated act on unchanged text does,
    would leave the reader recorded as shown only the text that is gone -- and the
    new text held behind a release the operator believes they just gave.
    """
    asked = ask(tmp_path, "alpha", "beta")
    first = requests.acknowledge(tmp_path, "beta", asked, now=1.0)
    let_through = requests.release(tmp_path, "operator", asked, now=1.0)
    rewrite(tmp_path, asked, body="reworded")
    (current,) = (r for r in requests.read(tmp_path).records if isinstance(r, requests.Request))

    second = requests.acknowledge(tmp_path, "beta", current, now=2.0)
    released = requests.release(tmp_path, "operator", current, now=3.0)

    assert isinstance(first, requests.Receipt) and isinstance(second, requests.Receipt)
    assert isinstance(let_through, requests.Release) and isinstance(released, requests.Release)
    assert (second.id != first.id, released.id != let_through.id) == (True, True)
    assert (second.digest, released.digest) == (requests.digest(current),) * 2
    derived = view(tmp_path, hold_from("alpha"))
    assert derived.threads[0].items[0].shown_to == ("beta",)
    assert derived.threads[0].items[0].delivery is Delivery.RELEASED
    assert stale(derived) == ["beta/entry-000001.json", "operator/entry-000001.json"]


def test_a_rewrite_that_changes_nothing_a_reader_is_shown_spends_nothing(tmp_path: Path) -> None:
    """
    The digest is of the record and not of the file. The same record laid out
    differently, or with a key this version does not read, is the text that was
    released.
    """
    asked = ask(tmp_path, "alpha", "beta")
    requests.release(tmp_path, "operator", asked, now=1.0)

    rewrite(tmp_path, asked, priority="high", created=100)

    derived = view(tmp_path, hold_from("alpha"))
    assert derived.threads[0].items[0].delivery is Delivery.RELEASED
    assert derived.problems == ()


def test_every_field_of_a_request_and_of_an_answer_is_in_its_digest() -> None:
    """
    A field left out of the digest is one its writer can change under an act that
    was made on the record. Walked off the dataclasses, so a field added later is
    in this test without anybody remembering to add it.
    """
    asked = requests.Request(
        "alpha", 1, "beta", "s", "b", Basis.READ, "bl", 1.0, "strict", True, (Quote("t", "src"),)
    )
    reply = requests.Answer(
        "beta", 1, asked.ref, requests.digest(asked), "b", Basis.RUN, 1.0, "strict", True, ()
    )
    other: dict[type, object] = {
        str: "different",
        int: 7,
        float: 2.5,
        bool: False,
        Basis: Basis.RELAYED,
        RequestRef: RequestRef("gamma", 9),
        tuple: (Quote("t", "elsewhere"),),
    }

    for record in (asked, reply):
        for field in fields(record):
            value = getattr(record, field.name)
            changed = replace(record, **{field.name: other[type(value)]})  # type: ignore[arg-type]
            assert changed != record, field.name
            assert requests.digest(changed) != requests.digest(record), field.name


# -- records that did not come through the write functions --------------------------


def test_a_close_written_by_somebody_else_closes_nothing(tmp_path: Path) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    force(tmp_path, requests.Close("beta", 1, asked.ref, 1.0))

    derived = view(tmp_path)

    assert derived.threads[0].state is State.OPEN
    assert derived.threads[0].close is None
    assert [(p.source, p.kind) for p in derived.problems] == [
        ("beta/entry-000001.json", ProblemKind.UNENTITLED)
    ]
    assert answer(tmp_path, "beta", asked) not in set(Refusal)


def test_an_answer_written_by_somebody_else_is_not_in_the_thread(tmp_path: Path) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    stray = requests.Answer(
        "gamma", 1, asked.ref, requests.digest(asked), "trust me", Basis.RUN, 1.0, None, None
    )
    force(tmp_path, stray)

    derived = view(tmp_path)

    assert derived.threads[0].state is State.OPEN
    assert [i.record for i in derived.threads[0].items] == [asked]
    assert requests.pending_for(derived, "alpha") == ()
    assert [(p.source, p.kind) for p in derived.problems] == [
        ("gamma/entry-000001.json", ProblemKind.UNENTITLED)
    ]
    # Nor can anybody be recorded as having been shown it.
    assert requests.acknowledge(tmp_path, "alpha", stray, now=1.0) is Refusal.NO_SUCH_RECORD


def test_records_that_refer_to_nothing_are_reported_and_change_nothing(tmp_path: Path) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    nowhere = RequestRef("alpha", 7)
    seen = requests.digest(asked)
    force(tmp_path, requests.Answer("beta", 1, nowhere, seen, "b", Basis.RUN, 1.0, None, None))
    force(tmp_path, requests.Close("alpha", 1, nowhere, 1.0))
    force(tmp_path, requests.Receipt("beta", 2, RecordId("alpha", "request-000007"), seen, 1.0))
    force(tmp_path, requests.Release("operator", 1, RecordId("alpha", "request-000007"), seen, 1.0))

    derived = view(tmp_path, HOLD_SOME)

    assert [t.ref for t in derived.threads] == [asked.ref]
    assert derived.threads[0].state is State.OPEN
    assert [(p.source, p.kind) for p in derived.problems] == [
        ("alpha/entry-000001.json", ProblemKind.ORPHAN),
        ("beta/entry-000001.json", ProblemKind.ORPHAN),
        ("beta/entry-000002.json", ProblemKind.ORPHAN),
        ("operator/entry-000001.json", ProblemKind.ORPHAN),
    ]


def test_two_closes_by_the_asker_are_one_closed_request(tmp_path: Path) -> None:
    """
    Two windows on one address both close. Neither is lost; the earlier is the
    thread's close.
    """
    asked = ask(tmp_path)
    first = requests.Close("alpha", 1, asked.ref, 9.0)
    second = requests.Close("alpha", 2, asked.ref, 1.0)
    force(tmp_path, second)
    force(tmp_path, first)

    thread = view(tmp_path).threads[0]

    assert thread.state is State.CLOSED
    assert thread.close == first


def test_an_answer_that_lands_after_the_close_stays_in_the_thread(tmp_path: Path) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    requests.close_request(tmp_path, "alpha", asked.ref, now=1.0)
    late = requests.Answer(
        "beta", 1, asked.ref, requests.digest(asked), "too late", Basis.RUN, 2.0, None, None
    )
    force(tmp_path, late)

    derived = view(tmp_path)

    assert derived.threads[0].state is State.CLOSED
    assert [i.record for i in derived.threads[0].items] == [asked, late]
    assert derived.problems == ()


# -- reading what is not a record -------------------------------------------------


def test_everything_unusable_is_reported_and_nothing_readable_is_dropped(tmp_path: Path) -> None:
    first = ask(tmp_path, "alpha", "beta")
    second = ask(tmp_path, "alpha", "beta")
    reply = answered(tmp_path, "beta", first)
    alpha, beta = tmp_path / "alpha", tmp_path / "beta"

    (tmp_path / "README").write_text("not a party")
    (tmp_path / ".DS_Store").write_text("skipped")
    (alpha / "notes.md").write_text("somebody's notes")
    (alpha / "request-7.json").write_text(json.dumps(requests._encode(first)))
    (alpha / "request-0000001.json").write_text(json.dumps(requests._encode(first)))
    (alpha / "request-000003.json").write_text("{not json")
    (alpha / "request-000004.json").write_text("[1, 2]")
    (alpha / "request-000005.json").write_text('{"kind": "telegram"}')
    (alpha / "request-000006.json").write_bytes(b"\xff\xfe")
    (alpha / "request-000007.json").mkdir()
    (alpha / "request-000008.json").write_text("[" * 100_000)
    (alpha / "entry-000001.json").write_text(json.dumps(requests._encode(first)))
    (beta / "entry-000002.json").write_text(
        (alpha / "request-000002.json").read_text().replace('"asker"', '"sender"')
    )
    (beta / "sub").mkdir()

    reading = requests.read(tmp_path)

    assert reading.records == (first, second, reply)
    assert {(p.source, p.kind) for p in reading.problems} == {
        ("README", ProblemKind.FOREIGN),
        ("alpha/notes.md", ProblemKind.FOREIGN),
        ("alpha/request-7.json", ProblemKind.FOREIGN),
        ("alpha/request-0000001.json", ProblemKind.FOREIGN),
        ("alpha/request-000003.json", ProblemKind.MALFORMED),
        ("alpha/request-000004.json", ProblemKind.MALFORMED),
        ("alpha/request-000005.json", ProblemKind.MALFORMED),
        ("alpha/request-000006.json", ProblemKind.MALFORMED),
        ("alpha/request-000007.json", ProblemKind.UNREADABLE),
        ("alpha/request-000008.json", ProblemKind.MALFORMED),
        ("alpha/entry-000001.json", ProblemKind.MISPLACED),
        ("beta/entry-000002.json", ProblemKind.MALFORMED),
        ("beta/sub", ProblemKind.FOREIGN),
    }
    assert len(reading.problems) == 13
    # And the numbers those names hold are not issued again.
    assert ask(tmp_path, "alpha", "beta").number == 9


def test_a_record_moved_or_renamed_is_not_read_as_what_its_new_name_says(tmp_path: Path) -> None:
    """
    Renaming a request file would renumber it, and copying it into another
    directory would make it another party's. Neither is taken at its word.
    """
    asked = ask(tmp_path, "alpha", "beta")
    source = tmp_path / "alpha" / "request-000001.json"
    (tmp_path / "gamma").mkdir()
    (tmp_path / "gamma" / "request-000001.json").write_bytes(source.read_bytes())
    (tmp_path / "alpha" / "request-000004.json").write_bytes(source.read_bytes())

    reading = requests.read(tmp_path)

    assert reading.records == (asked,)
    assert [(p.source, p.kind, p.detail) for p in reading.problems] == [
        ("alpha/request-000004.json", ProblemKind.MISPLACED, "content is alpha/request-000001"),
        ("gamma/request-000001.json", ProblemKind.MISPLACED, "content is alpha/request-000001"),
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("number", True),
        ("number", 0),
        ("number", -1),
        ("number", 1.0),
        ("number", "1"),
        ("created", True),
        ("created", "now"),
        ("created", None),
        # An integer JSON can spell and no float can hold.
        ("created", 10**400),
        ("asker", 7),
        ("basis", None),
        ("basis", "Run"),
        ("basis", "run\n"),
        ("basis", "guessed"),
        ("body", None),
        ("sender_policy", 3),
        ("sender_contained", "yes"),
        ("sender_contained", 1),
        ("quotes", None),
        ("quotes", ["said"]),
        ("quotes", [{"text": "said"}]),
    ],
)
def test_a_field_of_the_wrong_shape_is_a_malformed_record(
    tmp_path: Path, field: str, value: object
) -> None:
    asked = ask(tmp_path)
    path = tmp_path / "alpha" / "request-000001.json"
    data = requests._encode(asked)
    data[field] = value
    path.write_text(json.dumps(data))

    reading = requests.read(tmp_path)

    assert reading.records == ()
    assert [(p.source, p.kind) for p in reading.problems] == [
        ("alpha/request-000001.json", ProblemKind.MALFORMED)
    ]


def test_a_key_this_version_does_not_know_does_not_cost_the_record(tmp_path: Path) -> None:
    asked = ask(tmp_path)
    path = tmp_path / "alpha" / "request-000001.json"
    data = requests._encode(asked)
    data["priority"] = "high"
    data["created"] = 100
    path.write_text(json.dumps(data))

    assert requests.read(tmp_path) == requests.Reading((asked,), (), frozenset())


def read_within(root: Path, seconds: float) -> requests.Reading | None:
    """
    ``read`` on a worker, or None if it has not come back in time. A read that
    blocks cannot be interrupted, so the caller is left to unblock what it built.
    """
    out: list[requests.Reading] = []
    worker = threading.Thread(target=lambda: out.append(requests.read(root)), daemon=True)
    worker.start()
    worker.join(seconds)
    return out[0] if out else None


def test_a_fifo_where_a_record_belongs_does_not_hold_up_the_read(tmp_path: Path) -> None:
    """
    Opening a FIFO for reading waits for a writer. One party can make one in its
    own directory, and every party's read -- and so every party's write -- goes
    through that directory.
    """
    asked = ask(tmp_path, "alpha", "beta")
    fifo = tmp_path / "alpha" / "entry-000001.json"
    os.mkfifo(fifo)

    try:
        reading = read_within(tmp_path, 5.0)
        reply = answer(tmp_path, "beta", asked) if reading is not None else None
    finally:
        # Lets go of a reader that did block, so a failure here is a failure and
        # not a thread parked for the rest of the session.
        with contextlib.suppress(OSError):
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))

    assert reading is not None
    assert reading.records == (asked,)
    assert [(p.source, p.kind, p.detail) for p in reading.problems] == [
        ("alpha/entry-000001.json", ProblemKind.UNREADABLE, "not a regular file")
    ]
    assert isinstance(reply, requests.Answer)


def test_a_link_where_a_record_belongs_is_not_followed(tmp_path: Path) -> None:
    """
    To a device that never ends, to another party's record, to anything: a record
    is a regular file linked into place, and a symlink is not one this module made.
    """
    asked = ask(tmp_path, "alpha", "beta")
    (tmp_path / "gamma").mkdir()
    (tmp_path / "gamma" / "request-000001.json").symlink_to(
        tmp_path / "alpha" / "request-000001.json"
    )
    (tmp_path / "gamma" / "entry-000001.json").symlink_to("/dev/zero")

    reading = requests.read(tmp_path)

    assert reading.records == (asked,)
    assert [(p.source, p.kind) for p in reading.problems] == [
        ("gamma/entry-000001.json", ProblemKind.UNREADABLE),
        ("gamma/request-000001.json", ProblemKind.UNREADABLE),
    ]


def test_a_file_larger_than_a_record_may_be_is_reported_without_being_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    small = (tmp_path / "alpha" / "request-000001.json").stat().st_size
    huge = tmp_path / "alpha" / "entry-000001.json"
    with huge.open("wb") as handle:
        # Sparse: 64 MiB to a reader, nothing on the disk.
        handle.truncate(64 << 20)
    read = os.read
    taken: list[int] = []

    def counted(fd: int, length: int) -> bytes:
        chunk = read(fd, length)
        taken.append(len(chunk))
        return chunk

    monkeypatch.setattr(os, "read", counted)
    tracemalloc.start()
    try:
        reading = requests.read(tmp_path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert reading.records == (asked,)
    assert [(p.source, p.kind) for p in reading.problems] == [
        ("alpha/entry-000001.json", ProblemKind.UNREADABLE)
    ]
    # The request and not one byte of the other: its size was enough to refuse it.
    assert sum(taken) == small
    assert peak < requests.MAX_RECORD_BYTES


def test_a_file_that_is_longer_than_its_size_says_is_not_read_past_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The size is read off the descriptor and the file belongs to somebody who can
    append to it afterwards, so the limit has to hold in the read as well.
    """
    ask(tmp_path, "alpha", "beta")
    (tmp_path / "alpha" / "entry-000001.json").write_bytes(b" " * (4 << 20))
    fstat = os.fstat
    taken: list[int] = []
    read = os.read

    def empty_by_its_size(fd: int) -> os.stat_result:
        status = fstat(fd)
        return os.stat_result((status.st_mode, *status[1:6], 0, *status[7:10]))

    def counted(fd: int, length: int) -> bytes:
        chunk = read(fd, length)
        taken.append(len(chunk))
        return chunk

    with monkeypatch.context() as patch:
        patch.setattr(os, "fstat", empty_by_its_size)
        patch.setattr(os, "read", counted)
        reading = requests.read(tmp_path)

    assert [(p.source, p.kind) for p in reading.problems] == [
        ("alpha/entry-000001.json", ProblemKind.UNREADABLE)
    ]
    assert len(reading.records) == 1
    # The request, whole, and then one byte more than a record may hold.
    assert sum(taken) < 2 * requests.MAX_RECORD_BYTES


def test_the_largest_record_a_writer_may_write_is_one_a_reader_takes(tmp_path: Path) -> None:
    """
    The two limits are one number. A writer allowed a byte more than a reader takes
    would post a request its asker believes is waiting and nobody can read.
    """
    empty = ask(tmp_path / "measure", body="")
    overhead = (tmp_path / "measure" / "alpha" / "request-000001.json").stat().st_size
    room = requests.MAX_RECORD_BYTES - overhead

    largest = ask(tmp_path, body="x" * room)
    before = tree(tmp_path)
    too_large = requests.post_request(
        tmp_path,
        "alpha",
        to="beta",
        subject=empty.subject,
        body="x" * (room + 1),
        basis=empty.basis,
        blocks=empty.blocks,
        now=empty.created,
        sender_policy=empty.sender_policy,
        sender_contained=empty.sender_contained,
    )
    answer_too_large = answer(tmp_path, "beta", largest, body="x" * requests.MAX_RECORD_BYTES)

    assert (tmp_path / "alpha" / "request-000001.json").stat().st_size == requests.MAX_RECORD_BYTES
    assert too_large is Refusal.TOO_LARGE
    assert answer_too_large is Refusal.TOO_LARGE
    assert {name: data for name, data in tree(tmp_path).items() if "measure" not in name} == {
        name: data for name, data in before.items() if "measure" not in name
    }
    assert requests.read(tmp_path).records[0] == largest
    assert requests.next_number(tmp_path, "alpha") == 2


def test_a_directory_with_more_names_than_a_read_takes_is_skipped_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Reported, and none of it read: the first so-many would be whichever the
    directory listed first. The parties beside it are read as ever.
    """
    monkeypatch.setattr(requests, "MAX_ENTRIES", 5)
    for _ in range(4):
        ask(tmp_path, "alpha", "beta")
    kept = [ask(tmp_path, "beta", "alpha") for _ in range(4)]
    (tmp_path / "alpha" / "a-fifth").write_text("")
    at_the_limit = requests.read(tmp_path)
    assert len(at_the_limit.records) == 8 and at_the_limit.unread == frozenset()

    (tmp_path / "alpha" / "one-more").write_text("")

    reading = requests.read(tmp_path)
    assert reading.records == tuple(kept)
    assert [(p.source, p.kind) for p in reading.problems] == [("alpha", ProblemKind.UNREADABLE)]
    assert reading.unread == frozenset({"alpha"})


def _post(root: Path, asker: str = "alpha", **said: object) -> requests.Request | Refusal:
    """``post_request`` with nothing asserted about what comes back."""
    spoken: dict[str, object] = {
        "to": "beta",
        "subject": "s",
        "body": "b",
        "basis": Basis.READ,
        "blocks": "",
        "now": 1.0,
        "sender_policy": None,
        "sender_contained": None,
        **said,
    }
    return requests.post_request(root, asker, **spoken)  # type: ignore[arg-type]


def _names(directory: Path) -> int:
    return len(os.listdir(directory))


def test_a_writer_is_refused_before_its_directory_is_one_no_read_takes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Nothing deletes a record, so a directory fills by being used. The write that
    would fill it is refused as that, and every record a write did return is one
    the next read returns -- at the limit and at every moment on the way to it.
    """
    monkeypatch.setattr(requests, "MAX_ENTRIES", 6)
    most: list[int] = []
    link = os.link

    def counted(src: object, dst: object) -> None:
        link(src, dst)  # type: ignore[arg-type]
        # The temp file and the numbered name are both there now.
        most.append(_names(tmp_path / "alpha"))

    monkeypatch.setattr(os, "link", counted)

    written = []
    while isinstance(made := _post(tmp_path), requests.Request):
        written.append(made)
        assert len(written) < 20

    assert made is Refusal.FULL
    assert written and max(most) <= 6
    reading = requests.read(tmp_path)
    assert reading.records == tuple(written)
    assert reading.problems == () and reading.unread == frozenset()
    # Refused again, and again nothing written: the refusal is not a number spent.
    before = tree(tmp_path)
    assert _post(tmp_path) is Refusal.FULL
    assert tree(tmp_path) == before
    # Somebody else's directory is not full because this one is.
    assert isinstance(_post(tmp_path, "beta", to="alpha"), requests.Request)


def test_every_write_function_refuses_a_full_directory_as_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Each of the five writes into the writer's own directory, and each would
    otherwise return a record that the read after it does not.
    """
    asked = ask(tmp_path, "alpha", "beta")
    reply = answered(tmp_path, "beta", asked)
    second = ask(tmp_path, "alpha", "beta")
    monkeypatch.setattr(requests, "MAX_ENTRIES", 5)
    for party in ("alpha", "beta", "gamma", "operator"):
        (tmp_path / party).mkdir(exist_ok=True)
        while _names(tmp_path / party) < 4:
            (tmp_path / party / f"note-{_names(tmp_path / party)}").write_text("")
    assert requests.read(tmp_path).unread == frozenset()
    before = tree(tmp_path)

    assert _post(tmp_path, "alpha") is Refusal.FULL
    assert answer(tmp_path, "beta", second) is Refusal.FULL
    assert requests.close_request(tmp_path, "alpha", asked.ref, now=1.0) is Refusal.FULL
    assert requests.acknowledge(tmp_path, "gamma", reply, now=1.0) is Refusal.FULL
    assert requests.release(tmp_path, "operator", reply, now=1.0) is Refusal.FULL
    assert tree(tmp_path) == before


def test_a_new_party_is_refused_when_its_directory_would_put_the_root_past_a_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A read lists the root under the same limit and takes nothing from one past
    it. A first write makes a directory there, so it is the same refusal: one
    more party would cost every party its records.
    """
    monkeypatch.setattr(requests, "MAX_ENTRIES", 3)
    kept = [ask(tmp_path, asker, "alpha") for asker in ("alpha", "beta", "gamma")]

    assert _post(tmp_path, "delta") is Refusal.FULL
    assert not (tmp_path / "delta").exists()
    assert requests.read(tmp_path).records == tuple(kept)
    # A party already there still has its own directory to write in.
    assert isinstance(_post(tmp_path, "alpha"), requests.Request)

    (tmp_path / "stray").write_text("")
    assert requests.read(tmp_path).unread == frozenset({requests.EVERY_PARTY})
    assert _post(tmp_path, "alpha") is Refusal.FULL


def test_a_directory_that_was_not_read_is_named_and_nobody_else_is_blamed_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The asker's directory is past what a read takes. Its requests are not in the
    view, and what the other parties did to them is reported as unplaceable and
    not as theirs having been wrong: the answer names a request that is there.
    """
    hold_all = hold_from("alpha", "beta")
    asked = ask(tmp_path, "alpha", "beta")
    reply = answered(tmp_path, "beta", asked)
    assert isinstance(requests.acknowledge(tmp_path, "beta", asked, now=1.0), requests.Receipt)
    assert isinstance(requests.acknowledge(tmp_path, "gamma", reply, now=1.0), requests.Receipt)
    assert isinstance(requests.release(tmp_path, "operator", asked, now=1.0), requests.Release)
    assert isinstance(requests.release(tmp_path, "operator", reply, now=1.0), requests.Release)
    whole = view(tmp_path, hold_all)
    assert whole.problems == () and whole.unread == ()

    monkeypatch.setattr(requests, "MAX_ENTRIES", 5)
    for n in range(5):
        (tmp_path / "alpha" / f"note-{n}").write_text("")
    derived = view(tmp_path, hold_all)

    assert derived.unread == ("alpha",)
    assert derived.threads == ()
    assert [(p.source, p.kind) for p in derived.problems] == [
        ("beta/entry-000001.json", ProblemKind.UNRESOLVED),
        ("beta/entry-000002.json", ProblemKind.UNRESOLVED),
        ("gamma/entry-000001.json", ProblemKind.UNRESOLVED),
        ("operator/entry-000001.json", ProblemKind.UNRESOLVED),
        ("operator/entry-000002.json", ProblemKind.UNRESOLVED),
    ]
    assert "alpha" in derived.problems[0].detail


def test_an_operator_whose_directory_was_not_read_is_named_beside_what_it_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Every release is in one directory. With it unread each released record reads
    as held, which no record can be marked for: the view says whose directory is
    missing, and the same view of a directory that was read says nobody's.
    """
    hold_all = hold_from("alpha", "beta")
    asked = ask(tmp_path, "alpha", "beta")
    assert isinstance(requests.release(tmp_path, "operator", asked, now=1.0), requests.Release)
    assert [i.delivery for i in view(tmp_path, hold_all).threads[0].items] == [Delivery.RELEASED]

    monkeypatch.setattr(requests, "MAX_ENTRIES", 5)
    for n in range(5):
        (tmp_path / "operator" / f"note-{n}").write_text("")
    derived = view(tmp_path, hold_all)

    assert [i.delivery for i in derived.threads[0].items] == [Delivery.HELD]
    assert derived.unread == ("operator",)


def test_a_record_that_refers_to_nothing_in_a_directory_that_was_read_is_still_an_orphan(
    tmp_path: Path,
) -> None:
    """
    Unread is per directory. An answer to a number its asker never issued is the
    answerer's file being wrong, whoever else's directory could not be read.
    """
    asked = ask(tmp_path, "alpha", "beta")
    seen = requests.digest(asked)
    nowhere = RequestRef("alpha", 7)
    force(tmp_path, requests.Answer("beta", 1, nowhere, seen, "b", Basis.RUN, 1.0, None, None))
    force(tmp_path, requests.Receipt("beta", 2, RecordId("alpha", "request-000007"), seen, 1.0))
    records = requests.read(tmp_path).records

    def kinds(unread: frozenset[str]) -> list[ProblemKind]:
        derived = requests.derive(records, holds=hold_nothing, operators=OPERATORS, unread=unread)
        return [p.kind for p in derived.problems]

    assert kinds(frozenset({"gamma"})) == [ProblemKind.ORPHAN, ProblemKind.ORPHAN]
    assert kinds(frozenset({"alpha"})) == [ProblemKind.UNRESOLVED, ProblemKind.UNRESOLVED]
    assert kinds(frozenset({requests.EVERY_PARTY})) == [
        ProblemKind.UNRESOLVED,
        ProblemKind.UNRESOLVED,
    ]


def test_a_root_that_was_not_read_is_not_an_empty_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask(tmp_path, "alpha", "beta")
    monkeypatch.setattr(requests, "MAX_ENTRIES", 2)
    for n in range(2):
        (tmp_path / f"stray-{n}").write_text("")

    derived = view(tmp_path)

    assert derived.threads == () and derived.unread == (requests.EVERY_PARTY,)
    assert derived != view(tmp_path / "not-made-yet")


@pytest.mark.parametrize(
    "now", [100, 0, -3, 100.0, 1e308, float("inf"), 2**53 + 1, 2**70], ids=repr
)
def test_a_record_is_handed_back_as_a_reader_decodes_it(tmp_path: Path, now: float) -> None:
    """
    An integer is a float to the type checker and a different spelling to JSON,
    and a digest is taken over the spelling. Each write returns the record the
    disk holds, so everything done next is done to that.
    """
    asked = ask(tmp_path, "alpha", "beta", now=now)
    assert type(asked.created) is float
    reply = answered(tmp_path, "beta", asked, now=now)
    receipt = requests.acknowledge(tmp_path, "alpha", reply, now=now)
    released = requests.release(tmp_path, "operator", asked, now=now)
    closed = requests.close_request(tmp_path, "alpha", asked.ref, now=now)

    written = (asked, closed, reply, receipt, released)
    assert all(not isinstance(record, Refusal) for record in written)
    reading = requests.read(tmp_path)
    assert reading.problems == ()
    held = {record.id: record for record in reading.records}
    assert held == {record.id: record for record in written}  # type: ignore[union-attr]
    for returned in (asked, reply):
        on_disk = held[returned.id]
        assert isinstance(on_disk, requests.Request | requests.Answer)
        assert requests.digest(returned) == requests.digest(on_disk)
    assert stale(view(tmp_path)) == []


def test_quotes_handed_over_as_a_list_come_back_as_the_record_holds_them(tmp_path: Path) -> None:
    made = _post(tmp_path, quotes=[Quote("the words", "where")])

    assert isinstance(made, requests.Request)
    assert made.quotes == (Quote("the words", "where"),)
    assert requests.read(tmp_path).records == (made,)


@pytest.mark.parametrize(
    "said",
    [
        {"now": 10**400},
        {"now": True},
        {"sender_contained": 1},
        {"sender_policy": 7},
        {"subject": None},
    ],
    ids=repr,
)
def test_a_value_no_read_would_take_is_not_written(tmp_path: Path, said: dict[str, object]) -> None:
    """
    The encoder takes each of these and the decoder takes none. Written, it would
    be a request its asker was handed and every reader reports as malformed.
    """
    with pytest.raises(ValueError):
        _post(tmp_path, **said)

    assert tree(tmp_path) == {}


def test_a_name_of_a_series_shape_holds_its_number_in_its_own_directory_only(
    tmp_path: Path,
) -> None:
    """
    A read reports ``request-41.json`` as foreign, for its padding, and the count
    still takes 41 as issued. That moves the numbering of the directory the name
    is in and of no other, and a party writes in no directory but its own.
    """
    ask(tmp_path, "alpha", "beta")
    ask(tmp_path, "beta", "alpha")
    (tmp_path / "alpha" / "request-41.json").write_text("not a record")
    (tmp_path / "alpha" / "entry-0000000009.json").write_text("not a record")

    assert {p.kind for p in requests.read(tmp_path).problems} == {ProblemKind.FOREIGN}
    assert requests.next_number(tmp_path, "alpha") == 42
    assert requests.next_number(tmp_path, "beta") == 2
    closed = requests.close_request(tmp_path, "alpha", RequestRef("alpha", 1), now=1.0)
    assert isinstance(closed, requests.Close) and closed.serial == 10


def test_a_root_that_is_missing_is_an_empty_channel(tmp_path: Path) -> None:
    missing = tmp_path / "not-made-yet"

    assert requests.read(missing) == requests.Reading((), (), frozenset())
    assert view(missing) == requests.View((), (), ())
    assert answer(missing, "beta", phantom("alpha", 1)) is Refusal.NO_SUCH_REQUEST
    assert not missing.exists()


def test_a_root_that_is_not_a_directory_is_reported_not_raised(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.write_text("a file")

    reading = requests.read(root)

    assert reading.records == ()
    assert [p.kind for p in reading.problems] == [ProblemKind.UNREADABLE]
    assert reading.unread == frozenset({requests.EVERY_PARTY})


@pytest.mark.skipif(os.geteuid() == 0, reason="root lists a directory whatever its mode")
def test_a_directory_that_cannot_be_listed_is_unread_and_the_ones_beside_it_are_not(
    tmp_path: Path,
) -> None:
    asked = ask(tmp_path, "alpha", "beta")
    reply = answered(tmp_path, "beta", asked)
    (tmp_path / "alpha").chmod(0)
    try:
        reading = requests.read(tmp_path)
    finally:
        (tmp_path / "alpha").chmod(0o700)

    assert reading.records == (reply,)
    assert reading.unread == frozenset({"alpha"})
    assert [(p.source, p.kind) for p in reading.problems] == [("alpha", ProblemKind.UNREADABLE)]


def test_what_comes_back_does_not_depend_on_the_order_a_directory_lists_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = random.Random(7)
    scripted_run(tmp_path, rng, steps=60)
    (tmp_path / "alpha" / "notes.md").write_text("foreign")
    (tmp_path / "beta" / "request-000099.json").write_text("{")
    expected = requests.read(tmp_path)
    assert len(expected.records) > 20 and len(expected.problems) == 2

    for seed in range(20):
        order = random.Random(seed)

        def shuffled(directory: Path, order: random.Random = order) -> list[str]:
            names = os.listdir(directory)
            order.shuffle(names)
            return names

        with monkeypatch.context() as patch:
            patch.setattr(requests, "_listing", shuffled)
            assert requests.read(tmp_path) == expected


# -- the invariants, over random runs ---------------------------------------------

PARTIES = ("alpha", "beta", "gamma", "operator")


def scripted_run(
    root: Path,
    rng: random.Random,
    *,
    steps: int,
    after: Callable[[str, dict[str, bytes], dict[str, bytes]], None] | None = None,
) -> None:
    """
    A run of the write functions by several parties, right and wrong alike.

    Each step picks a party and an operation, aiming at things that exist, things
    that do not and things that are not that party's to touch, so the refused
    paths are exercised as often as the admitted ones. ``after`` is handed the
    actor and the tree either side of each step.
    """
    clock = 1000.0
    for _ in range(steps):
        actor = rng.choice(PARTIES)
        records = requests.read(root).records
        asked = [r for r in records if isinstance(r, requests.Request)]
        asked.append(phantom(rng.choice(PARTIES), rng.randint(1, 40)))
        refs = [r.ref for r in asked]
        # Closes, receipts and releases among them: a caller can name anything.
        seen = [*records, phantom(rng.choice(PARTIES), rng.randint(1, 40))]
        clock += rng.uniform(-5.0, 5.0)
        contained = rng.choice([True, False, None])
        before = tree(root) if after is not None else {}

        match rng.randrange(6):
            case 0 | 1:
                requests.post_request(
                    root,
                    actor,
                    to=rng.choice(PARTIES),
                    subject="s",
                    body=f"body {clock}",
                    basis=Basis.READ,
                    blocks="",
                    now=clock,
                    sender_policy=rng.choice(["strict", "autonomous", None]),
                    sender_contained=contained,
                )
            case 2:
                requests.post_answer(
                    root,
                    actor,
                    rng.choice(asked),
                    body=f"answer {clock}",
                    basis=Basis.RUN,
                    now=clock,
                    sender_policy=None,
                    sender_contained=contained,
                )
            case 3:
                requests.close_request(root, actor, rng.choice(refs), now=clock)
            case 4:
                requests.acknowledge(root, actor, rng.choice(seen), now=clock)  # type: ignore[arg-type]
            case _:
                requests.release(root, actor, rng.choice(seen), now=clock)  # type: ignore[arg-type]

        if after is not None:
            after(actor, before, tree(root))


@pytest.mark.parametrize("seed", range(25))
def test_a_party_writes_only_in_its_own_directory_and_no_record_ever_changes(
    tmp_path: Path, seed: int
) -> None:
    """
    After every step, by every party, whether admitted or refused: each file that
    existed still holds the same bytes, and each new file is the actor's.
    """
    steps = 0
    grew = 0

    def check(actor: str, before: dict[str, bytes], now: dict[str, bytes]) -> None:
        nonlocal steps, grew
        steps += 1
        assert {name: now.get(name) for name in before} == before
        added = set(now) - set(before)
        assert all(name.startswith(f"{actor}/") for name in added), (actor, added)
        assert not any("/." in name for name in now)
        assert len(added) <= 1
        grew += len(added)

    scripted_run(tmp_path, random.Random(seed), steps=80, after=check)

    # The run has to have both written and been refused for the above to mean much.
    assert steps == 80
    assert 20 < grew < 80


def acts_on(derived: requests.View, target: RecordId) -> list[str]:
    """
    Everything ``derived`` credits to the record ``target``: who it was shown to,
    whether an operator let it through, and what answers it if it is a request.
    """
    out: list[str] = []
    for thread in derived.threads:
        for item in thread.items:
            if item.record.id != target:
                continue
            out.extend(f"shown to {party}" for party in item.shown_to)
            if item.delivery is Delivery.RELEASED:
                out.append("released")
            if isinstance(item.record, requests.Request):
                out.extend(f"answered by {i.record.id.name}" for i in thread.items[1:])
    return out


@pytest.mark.parametrize("seed", range(25))
def test_a_record_its_writer_changes_keeps_nothing_anybody_did_to_it(
    tmp_path: Path, seed: int
) -> None:
    """
    The other half of the test above. No write function changes a record, and a
    party can still change its own with an editor: after it does, the state derived
    from the disk credits the new text with no receipt, no release and no answer,
    under a rule that holds everything so that a release is always visible.
    """
    hold_all = hold_from(*PARTIES)
    operators = frozenset(PARTIES)

    def derived() -> requests.View:
        reading = requests.read(tmp_path)
        return requests.derive(
            reading.records, holds=hold_all, operators=operators, unread=reading.unread
        )

    scripted_run(tmp_path, random.Random(seed), steps=120)
    targets = [
        item.record
        for thread in derived().threads
        for item in thread.items
        if acts_on(derived(), item.record.id)
    ]
    # The run has to have left something to lose.
    assert len(targets) >= 3

    for target in targets:
        current = next(r for r in requests.read(tmp_path).records if r.id == target.id)
        assert isinstance(current, requests.Request | requests.Answer)
        rewrite(tmp_path, current, body=f"{current.body}, reworded")

        assert acts_on(derived(), target.id) == []


@pytest.mark.parametrize("seed", range(25))
def test_the_derived_state_is_the_same_for_the_same_records_in_any_order(
    tmp_path: Path, seed: int
) -> None:
    rng = random.Random(seed)
    scripted_run(tmp_path, rng, steps=80)
    # Records no write function would have made, so the re-checked rules are in play.
    force(tmp_path, requests.Close("gamma", 900, RequestRef("alpha", 1), 1.0))
    force(tmp_path, requests.Close("alpha", 901, RequestRef("alpha", 1), 1.0))
    force(tmp_path, requests.Close("alpha", 902, RequestRef("alpha", 1), 1.0))
    force(
        tmp_path,
        requests.Answer(
            "gamma", 903, RequestRef("beta", 1), "0" * 64, "b", Basis.RUN, 1.0, None, None
        ),
    )
    force(
        tmp_path, requests.Release("beta", 904, RecordId("alpha", "request-000001"), "0" * 64, 1.0)
    )
    # And one rewritten by its own writer after others had acted on it.
    rewritten = next(r for r in requests.read(tmp_path).records if isinstance(r, requests.Request))
    rewrite(tmp_path, rewritten, body="not what anybody answered")
    records = list(requests.read(tmp_path).records)

    for holds in (hold_nothing, HOLD_SOME):
        expected = requests.derive(records, holds=holds, operators=OPERATORS, unread=frozenset())
        assert len(expected.threads) > 5
        assert len(expected.problems) >= 2
        for _ in range(10):
            rng.shuffle(records)
            derived = requests.derive(records, holds=holds, operators=OPERATORS, unread=frozenset())
            assert derived == expected
            for party in PARTIES:
                assert requests.pending_for(derived, party) == requests.pending_for(expected, party)
                assert requests.held_for(derived, party) == requests.held_for(expected, party)


@pytest.mark.parametrize("seed", range(25))
def test_any_interleaving_of_two_parties_writes_derives_the_same_state(
    tmp_path: Path, seed: int
) -> None:
    """
    Each party's own records in its own order; the two orders merged every which
    way, including ones where an answer reaches the disk before the request it
    answers. The files, and so the state, come out the same.
    """
    rng = random.Random(seed)
    source = tmp_path / "source"
    scripted_run(source, rng, steps=80)
    by_party: dict[str, list[requests.Record]] = {}
    for record in requests.read(source).records:
        by_party.setdefault(record.id.party, []).append(record)
    for theirs in by_party.values():
        # Within a series a party's order is its numbering; across its two series
        # any merge that keeps both in order is one it could have written.
        theirs.sort(key=lambda r: (r.id.name.split("-")[1], r.id.name))
    expected_tree = tree(source)
    expected = view(source, HOLD_SOME)
    assert len(expected.threads) > 5

    for attempt in range(6):
        root = tmp_path / f"interleaving-{attempt}"
        queues = {party: list(theirs) for party, theirs in by_party.items()}
        while queues:
            party = rng.choice(sorted(queues))
            record = queues[party].pop(0)
            if not queues[party]:
                del queues[party]
            series = record.id.name.split("-")[0]

            def same(serial: int, record: requests.Record = record) -> requests.Record:
                return record

            written = requests._append(root / party, series, same)
            assert written.id == record.id

        assert tree(root) == expected_tree
        assert view(root, HOLD_SOME) == expected


def test_derivation_reads_no_clock(tmp_path: Path) -> None:
    """
    The same exchange under two sets of timestamps -- one party's clock an hour
    ahead in the first, an hour behind in the second -- has one shape.
    """

    def run(root: Path, skew: float) -> list[tuple[State, list[RecordId], list[Delivery]]]:
        asked = ask(root, "alpha", "beta", now=1000.0)
        answered(root, "beta", asked, now=1000.0 + skew)
        answered(root, "beta", asked, now=1000.0 - skew)
        other = ask(root, "alpha", "beta", now=5.0)
        requests.close_request(root, "alpha", other.ref, now=1000.0 - 2 * skew)
        return [
            (t.state, [i.record.id for i in t.items], [i.delivery for i in t.items])
            for t in view(root, HOLD_SOME).threads
        ]

    assert run(tmp_path / "ahead", 3600.0) == run(tmp_path / "behind", -3600.0)
