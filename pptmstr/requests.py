"""
Numbered requests between sessions: records on disk, state derived on read.

**The records are the channel; there is no state file.** A request is open, answered
or closed because of which files exist, and for no other reason. That is ``STYLE.md``
§1's *derive; do not store* across a process boundary, and it is the argument of
2026-08-31 D1 at the point of use: an index written by this application has the same
failure mode as this application, and the reader of a request is by construction a
different process from its writer, possibly one that outlived it. So nothing here
writes a summary, a counter or a "last seen" marker. The next number is the highest
name in a directory plus one; what a party has been shown is the receipts in that
party's own directory.

**A party writes only inside its own directory.** An answer lives with the answerer
and names the request it answers; a receipt lives with the reader; a close lives with
the asker. Two parties therefore never contend for one name, which leaves exactly one
race -- two writers holding the same address -- and that one is closed by the write
primitive rather than by a lock. Every write in this module resolves its target from
``(root, writer)`` and nothing else, so there is no argument a caller can pass that
moves a write into another party's directory.

**A record is linked into place, not renamed into place.** ``os.replace`` onto a name
that exists destroys what was there with nothing raised, and a name derived from a
listing is exactly how two writers arrive at the same one. Each record is written
whole to a temp file in the target directory and then ``os.link``-ed to its numbered
name, which fails atomically when the name is taken; the loser takes the next number.
The numbered name appears with its full content or not at all, so a reader never sees
half a record, and a number is taken only by a complete record: a writer that dies
before the link leaves a temp file readers skip and no number spent. The cost is a
filesystem that supports hard links, and ``OSError`` on one that does not.

**The root is assumed to be on a local filesystem.** "The name is taken" is read off
the link failing, which is the whole truth only where a link that fails did not
happen. A network filesystem can report a link as failed after it landed -- the reply
is lost and the retry finds the name there -- and the writer here would then write
the same record again under the next number. Nothing in this module detects that.

**A reference to a request is the pair (asker, number).** Numbers are per asker, so a
bare number names a different request in every directory.

**This module authenticates nothing.** A record's writer is the directory it was
found in, which is as trustworthy as that directory's permissions and no more. A
record claiming a writer other than its directory is reported and dropped, which
catches a copied file and does not catch a forger who writes in the right place. A
concern on the bus has a sender because the gate stamped one; a request on disk has
the sender its writer chose, and whatever renders one owes its reader that difference.

**An act on somebody else's record names the content it was made against.** A party
can rewrite any file in its own directory, and nothing here can stop it. So an
answer carries the digest of the request it answers, and a receipt and a release
carry the digest of the record they are for (``digest``). ``derive`` counts each only
while the record it names still has that content, and reports it as ``STALE``
otherwise: a request rewritten after it was answered is an open request with a stale
answer beside it, held again if it was released and pending again if it was shown.
The write functions are handed the record *as the caller saw it* for the same reason
-- a reference looked up again at the write would bind whatever is on disk by then.
A close binds nothing: it is the asker's act on the asker's own request, so there is
no second party whose act a rewrite could carry.

**One party cannot make another's read unbounded.** A read opens nothing that can
block and follows no link, takes bytes only from a regular file and at most
``MAX_RECORD_BYTES`` of one, and skips a directory holding more than ``MAX_ENTRIES``
names. Each of those is reported, and a directory skipped whole is named in the
``Reading`` and again in the ``View`` derived from it: what its party wrote is not
known, which is a different thing from its party having written nothing.

**A write that returns a record has written one a read takes.** The write functions
refuse a record over the size a read would take, refuse to write into a directory
at the number of names a read would take, and hand back the record as it decodes
from the bytes they wrote and not as it was built -- so the digest of what a writer
was given is the digest every reader computes.

**Three questions are left to the caller, as parameters.** What addresses a party is
an opaque string, checked only for being safe as one path component. Where the root
lives is an argument to every function. And what may reach its addressee without a
person is the ``holds`` rule handed to ``derive``: a ``Release`` exists for an operator
to write, and ``derive`` reports held, released and delivered as three different
things, so the channel can be wired to hold everything, nothing, or anything between
without a change here.

**A hold rule may key on who wrote a record and on nothing the record says about
its writer.** The rule is handed the record, and the one thing in it the writer did
not choose is whose directory it was found in -- ``asker`` or ``answerer``, which
``read`` checks against the location. ``sender_policy`` and ``sender_contained`` are
the sender's own statement, there for whoever reads the text; a rule that lets a
record through because of them is one any sender satisfies by writing the file. A
caller that knows how a session is gated knows it from its own records, by address.
"""

from __future__ import annotations

import contextlib
import enum
import hashlib
import itertools
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, assert_never, cast

from .model import Basis

# Two series per directory, numbered apart. Requests have their own so that an
# asker's numbers run 1, 2, 3 whatever else it wrote in between: a gap in a request
# series reads as a request that went missing.
_REQUEST = "request"
_ENTRY = "entry"

# `request-000007.json`. Zero-padded so `ls` lists a series in order; a series that
# outgrows the width keeps working, because every ordering here is by the integer.
_WIDTH = 6
_NAME = re.compile(r"(request|entry)-(\d+)\.json")

# NAME_MAX on every filesystem this runs on, in bytes.
_MAX_COMPONENT = 255

# The most one record file may hold, in bytes, for a writer and a reader alike: a
# write over it is refused and a file over it is not read. It is what keeps the
# memory one read takes a property of this module and not of whatever a party left
# in its directory. A megabyte is a few hundred pages of quoted text.
MAX_RECORD_BYTES = 1 << 20

# The most names read from one directory. A directory holding more is reported and
# none of it is read: taking the first so-many would make which records exist
# depend on the order the directory happened to list in.
MAX_ENTRIES = 100_000


class Refusal(enum.Enum):
    """
    Why a write was not made. One member per mistake, so the caller can say which.
    """

    # The address is not usable as a directory name.
    UNSAFE_ADDRESS = "unsafe_address"
    # No request has that asker and number. A misspelled asker and a number that
    # was never issued both land here: either way the pair names nothing on disk.
    NO_SUCH_REQUEST = "no_such_request"
    # The request exists and is addressed to somebody else.
    NOT_ADDRESSED_TO_YOU = "not_addressed_to_you"
    # The request exists and somebody else asked it; only its asker closes it.
    NOT_YOURS_TO_CLOSE = "not_yours_to_close"
    # The asker has closed it.
    ALREADY_CLOSED = "already_closed"
    # A receipt or a release named a record that is not a request or an answer on
    # disk.
    NO_SUCH_RECORD = "no_such_record"
    # The record is on disk and no longer says what the caller was shown. Apart from
    # the two above because the remedy differs: read again, and decide again.
    CHANGED = "changed"
    # The record would be larger than a reader takes.
    TOO_LARGE = "too_large"
    # The writer's directory, or the root, holds as many names as a reader takes.
    # Apart from ``TOO_LARGE`` because nothing about the record would change it.
    FULL = "full"


class ProblemKind(enum.Enum):
    # The file or directory could not be read, or was not: it is not a regular
    # file, or holds more than a read takes.
    UNREADABLE = "unreadable"
    # Not written by this module: a name outside the two series, or a file where a
    # directory belongs.
    FOREIGN = "foreign"
    # One of ours by name, and its content is not a record.
    MALFORMED = "malformed"
    # A record whose content disagrees with where it was found: another writer's
    # name, another number, or the wrong series. A renamed or copied file.
    MISPLACED = "misplaced"
    # A record that refers to a request or record that is not there.
    ORPHAN = "orphan"
    # A record that refers to one in a directory that was not read. Whether it is
    # there is not known, and nothing about that is the referring record's doing.
    UNRESOLVED = "unresolved"
    # A record its writer had no standing to write: a close by somebody other than
    # the asker, an answer by somebody other than the addressee, a release by a
    # writer the caller did not name as an operator.
    UNENTITLED = "unentitled"
    # An answer, a receipt or a release whose record is there and no longer has the
    # content it was made against.
    STALE = "stale"


class State(enum.Enum):
    OPEN = "open"
    ANSWERED = "answered"
    CLOSED = "closed"


class Delivery(enum.Enum):
    """
    Whether a record has reached the party it is for.

    ``RELEASED`` is kept apart from ``DELIVERED`` because they are different facts
    for whoever renders it: one arrived on its own, the other because an operator
    let it through.
    """

    DELIVERED = "delivered"
    HELD = "held"
    RELEASED = "released"


@dataclass(frozen=True, slots=True, order=True)
class RequestRef:
    asker: str
    number: int


@dataclass(frozen=True, slots=True, order=True)
class RecordId:
    """
    One record on disk: whose directory, and the file's name without its suffix.
    """

    party: str
    name: str


@dataclass(frozen=True, slots=True)
class Quote:
    """
    Somebody's words, verbatim, and where they were said.

    Stored and returned byte for byte. Nothing here trims or normalises ``text``:
    a quote that has been tidied is a paraphrase.
    """

    text: str
    source: str


@dataclass(frozen=True, slots=True)
class Request:
    asker: str
    number: int
    to: str
    subject: str
    body: str
    # How the asker says it knows what the body asserts. Not optional here, unlike
    # on the board: a claim leaving its session is read by somebody with no other
    # way to weigh it.
    basis: Basis
    # What the asker cannot do until this is answered, in the asker's words.
    blocks: str
    # Wall clock. For a reader; no derivation here orders by it.
    created: float
    # The sender's gate policy and whether it ran contained, as the sender states
    # them. None is "did not say"; it is not a spelling of either answer. For a
    # reader, beside the text. Not for a hold rule: the sender wrote them.
    sender_policy: str | None
    sender_contained: bool | None
    quotes: tuple[Quote, ...] = ()

    @property
    def ref(self) -> RequestRef:
        return RequestRef(self.asker, self.number)

    @property
    def id(self) -> RecordId:
        return RecordId(self.asker, _stem(_REQUEST, self.number))


@dataclass(frozen=True, slots=True)
class Answer:
    answerer: str
    serial: int
    ref: RequestRef
    # ``digest`` of the request as the answerer was shown it.
    answers: str
    body: str
    basis: Basis
    at: float
    sender_policy: str | None
    sender_contained: bool | None
    quotes: tuple[Quote, ...] = ()

    @property
    def id(self) -> RecordId:
        return RecordId(self.answerer, _stem(_ENTRY, self.serial))


@dataclass(frozen=True, slots=True)
class Close:
    """
    The asker's statement that a request is finished with.

    It carries no text. A close is a change of state and nothing else, so there is
    nothing in one that a hold rule would have to consider.

    It carries no digest either. The request is the asker's and so is the close, so
    a rewrite of one cannot put anybody else's act on words they did not see.
    """

    asker: str
    serial: int
    ref: RequestRef
    at: float

    @property
    def id(self) -> RecordId:
        return RecordId(self.asker, _stem(_ENTRY, self.serial))


@dataclass(frozen=True, slots=True)
class Receipt:
    """
    ``reader`` has been shown the record ``shown``.
    """

    reader: str
    serial: int
    shown: RecordId
    # ``digest`` of that record as the reader was shown it.
    digest: str
    at: float

    @property
    def id(self) -> RecordId:
        return RecordId(self.reader, _stem(_ENTRY, self.serial))


@dataclass(frozen=True, slots=True)
class Release:
    """
    An operator lets a held record through.

    Written in the operator's own directory like any other record. What makes its
    writer an operator is the ``operators`` set handed to ``derive``, not anything in
    the file: a release found in a directory the caller did not name counts for
    nothing and is reported.
    """

    operator: str
    serial: int
    released: RecordId
    # ``digest`` of that record as the operator let it through.
    digest: str
    at: float

    @property
    def id(self) -> RecordId:
        return RecordId(self.operator, _stem(_ENTRY, self.serial))


Record = Request | Answer | Close | Receipt | Release

_R = TypeVar("_R", bound=Record)

# True for a record that must wait for an operator. The rule is the caller's.
HoldRule = Callable[[Request | Answer], bool]


@dataclass(frozen=True, slots=True)
class Problem:
    """
    Something under the root that could not be used, and why.

    ``source`` is ``party/file`` relative to the root, for a file and for a record
    alike, so a problem found while reading and one found while deriving name the
    same thing the same way.
    """

    source: str
    kind: ProblemKind
    detail: str


# In ``Reading.unread`` and ``View.unread``: the root itself was not read, so no
# party's directory was. Not an address -- ``address_refusal`` refuses it.
EVERY_PARTY = ""


@dataclass(frozen=True, slots=True)
class Reading:
    records: tuple[Record, ...]
    problems: tuple[Problem, ...]
    # The parties whose directory was not read at all, or ``EVERY_PARTY``. Each is
    # in ``problems`` too; it is apart from them because ``derive`` needs it, and a
    # caller that hands on only the records has to be made to hand this on as well.
    unread: frozenset[str]


@dataclass(frozen=True, slots=True)
class Item:
    """
    A request or an answer as a reader should see it.
    """

    record: Request | Answer
    delivery: Delivery
    # Every party with a receipt for this record, sorted.
    shown_to: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Thread:
    """
    One request and what followed it.

    ``items`` is the request and then its answers. Every answer is in the
    addressee's directory, so their order is that directory's numbering and no
    clock is consulted. The close is held apart from them because it is in a
    different directory: whether it came before or after a given answer is a
    comparison of two parties' wall clocks, which this does not pretend to make.
    """

    request: Request
    state: State
    items: tuple[Item, ...]
    close: Close | None

    @property
    def ref(self) -> RequestRef:
        return self.request.ref


@dataclass(frozen=True, slots=True)
class View:
    # Ordered by (asker, number).
    threads: tuple[Thread, ...]
    problems: tuple[Problem, ...]
    # The parties whose directory was not read, sorted, or ``EVERY_PARTY``. Every
    # thread and delivery here is short by whatever those parties wrote: a request
    # of theirs is absent, an answer of theirs leaves its request ``OPEN``, and a
    # release of theirs leaves what it released ``HELD``.
    unread: tuple[str, ...]

    def thread(self, ref: RequestRef) -> Thread | None:
        return next((t for t in self.threads if t.ref == ref), None)


def digest(record: Request | Answer) -> str:
    """
    What an answer, a receipt and a release bind: every field of ``record`` that
    this module reads, as one SHA-256.

    Of the record and not of the file's bytes, so that ``derive`` stays a function
    of the records alone. Two files that decode to the same record are the same
    content to every reader here, and a key this version does not read is one it
    shows nobody. An answer's digest covers the digest of the request it answers.

    **The digest has no version, so the set of fields it covers cannot change.**
    Every answer, receipt and release on disk holds a digest computed over exactly
    the keys ``_encode`` emits for a request and for an answer. A field added to
    either, or a change in how one is spelled, gives every existing record a new
    digest, and ``derive`` then reports everything ever done to it as ``STALE``.
    """
    canonical = json.dumps(
        _encode(record), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def address_refusal(address: str) -> Refusal | None:
    """
    ``UNSAFE_ADDRESS`` unless the string is usable as one directory name.

    That is the whole of what an address is checked for. A leading dot is refused
    along with the separators because dot-names under the root are this module's
    temp files and whatever else a reader is meant to skip.
    """
    if (
        not address
        or address.startswith(".")
        or any(c in address for c in ("/", "\\", "\0"))
        or (os.altsep is not None and os.altsep in address)
    ):
        return Refusal.UNSAFE_ADDRESS
    try:
        encoded = os.fsencode(address)
    except UnicodeEncodeError:
        return Refusal.UNSAFE_ADDRESS
    if len(encoded) > _MAX_COMPONENT:
        return Refusal.UNSAFE_ADDRESS
    return None


def next_number(root: Path, asker: str) -> int:
    """
    The number the asker's next request would take, were nobody else writing.

    From the names in the asker's directory and nothing else. A request file that
    cannot be read or parsed still holds its number: the name is what was issued.
    """
    return _next_serial(root / asker, _REQUEST)


def post_request(
    root: Path,
    asker: str,
    *,
    to: str,
    subject: str,
    body: str,
    basis: Basis,
    blocks: str,
    now: float,
    sender_policy: str | None,
    sender_contained: bool | None,
    quotes: tuple[Quote, ...] = (),
) -> Request | Refusal:
    """
    Write a request in the asker's directory under its next number.

    The addressee needs no directory. A request is found by reading the asker's,
    so a party that has written nothing can still be asked.

    ``sender_policy`` and ``sender_contained`` have no default. They are for the
    caller that knows them to state, and an omitted argument would be recorded
    exactly like "did not know".

    Raises ``OSError`` when the record cannot be written, as ``brief.write_entry``
    does and for its reason: a request that silently failed to persist is one the
    asker believes is waiting on somebody. One too large for a reader to take is
    refused for that reason too, and not written, and so is one into a directory
    already holding as many names as a reader takes (``FULL``).

    What comes back is the request as a reader decodes it, which is what an answer,
    a receipt and a release have to be made against: ``now`` given as an integer is
    a float in it. Raises ``ValueError``, having written nothing, for a value that
    would not read back as a record at all.
    """
    refusal = address_refusal(asker) or address_refusal(to)
    if refusal is not None:
        return refusal

    def make(number: int) -> Request:
        return Request(
            asker=asker,
            number=number,
            to=to,
            subject=subject,
            body=body,
            basis=basis,
            blocks=blocks,
            created=now,
            sender_policy=sender_policy,
            sender_contained=sender_contained,
            quotes=quotes,
        )

    return _written(root / asker, _REQUEST, make)


def post_answer(
    root: Path,
    answerer: str,
    request: Request,
    *,
    body: str,
    basis: Basis,
    now: float,
    sender_policy: str | None,
    sender_contained: bool | None,
    quotes: tuple[Quote, ...] = (),
) -> Answer | Refusal:
    """
    Write an answer in the answerer's directory.

    ``request`` is the request as the answerer was shown it, and the answer is bound
    to that content. If what is on disk under its number is no longer that, nothing
    is written and the refusal is ``CHANGED``.

    A request may be answered more than once; each answer is its own record and the
    thread keeps them in the order they were written.

    The check and the write are not one step. An asker can close between them, and
    the answer then lands on a closed request; ``derive`` keeps it in the thread
    and the request stays closed.
    """
    refusal = address_refusal(answerer) or answer_refusal(read(root).records, answerer, request)
    if refusal is not None:
        return refusal
    answers = digest(request)

    def make(serial: int) -> Answer:
        return Answer(
            answerer=answerer,
            serial=serial,
            ref=request.ref,
            answers=answers,
            body=body,
            basis=basis,
            at=now,
            sender_policy=sender_policy,
            sender_contained=sender_contained,
            quotes=quotes,
        )

    return _written(root / answerer, _ENTRY, make)


def close_request(root: Path, party: str, ref: RequestRef, *, now: float) -> Close | Refusal:
    """
    Write a close in the directory of the party asking for it.
    """
    refusal = address_refusal(party) or close_refusal(read(root).records, party, ref)
    if refusal is not None:
        return refusal
    return _written(
        root / party, _ENTRY, lambda serial: Close(asker=party, serial=serial, ref=ref, at=now)
    )


def acknowledge(
    root: Path, reader: str, shown: Request | Answer, *, now: float
) -> Receipt | Refusal:
    """
    Record that ``reader`` has been shown a request or an answer.

    ``shown`` is the record as it was shown, and the receipt is for that content.
    If what is on disk under its name is no longer that, nothing is written and the
    refusal is ``CHANGED``: the reader has not been shown what is there.

    Any party may hold a receipt for any record: being shown something is a fact
    about the reader, and who a record is *for* is ``pending_for``'s question.
    A second acknowledgement of the same content returns the receipt already on disk
    and writes nothing.
    """
    refusal = address_refusal(reader)
    if refusal is not None:
        return refusal
    records = read(root).records
    refusal = _seen_refusal(records, shown)
    if refusal is not None:
        return refusal
    seen = digest(shown)
    for record in records:
        if (
            isinstance(record, Receipt)
            and record.reader == reader
            and record.shown == shown.id
            and record.digest == seen
        ):
            return record
    return _written(
        root / reader,
        _ENTRY,
        lambda serial: Receipt(reader=reader, serial=serial, shown=shown.id, digest=seen, at=now),
    )


def release(
    root: Path, operator: str, released: Request | Answer, *, now: float
) -> Release | Refusal:
    """
    Record that an operator lets a request or an answer through.

    ``released`` is the record as the operator read it, and the release is of that
    content and no other: ``CHANGED`` when what is on disk is no longer that.

    Whether the record was held is not checked, because what is held is a rule
    this function is not given. A release of something no rule holds changes
    nothing that ``derive`` reports.
    """
    refusal = address_refusal(operator)
    if refusal is not None:
        return refusal
    records = read(root).records
    refusal = _seen_refusal(records, released)
    if refusal is not None:
        return refusal
    seen = digest(released)
    for record in records:
        if (
            isinstance(record, Release)
            and record.operator == operator
            and record.released == released.id
            and record.digest == seen
        ):
            return record
    return _written(
        root / operator,
        _ENTRY,
        lambda serial: Release(
            operator=operator, serial=serial, released=released.id, digest=seen, at=now
        ),
    )


def answer_refusal(records: Iterable[Record], answerer: str, request: Request) -> Refusal | None:
    """
    Why ``answerer`` may not answer ``request`` as it was shown it, or None.

    Pure, and separate from the write so the rule can be exercised without a
    filesystem. A request that has changed is refused as that before anything
    else about it is judged: every other answer here would be about words the
    caller has not read.
    """
    records = tuple(records)
    current = _request(records, request.ref)
    if current is None:
        return Refusal.NO_SUCH_REQUEST
    if digest(current) != digest(request):
        return Refusal.CHANGED
    if current.to != answerer:
        return Refusal.NOT_ADDRESSED_TO_YOU
    if request.ref in _closed(records):
        return Refusal.ALREADY_CLOSED
    return None


def close_refusal(records: Iterable[Record], party: str, ref: RequestRef) -> Refusal | None:
    """
    Why ``party`` may not close ``ref``, or None.

    Somebody else's request is refused as theirs whether or not it exists: the
    pair names its asker, so the caller's mistake is the same one either way.
    """
    records = tuple(records)
    if ref.asker != party:
        return Refusal.NOT_YOURS_TO_CLOSE
    if _request(records, ref) is None:
        return Refusal.NO_SUCH_REQUEST
    if ref in _closed(records):
        return Refusal.ALREADY_CLOSED
    return None


def read(root: Path) -> Reading:
    """
    Every record readable under the root, and everything that was not one.

    Nothing raises. A root that does not exist is an empty channel. A file that
    cannot be read, is not JSON, is not a record, or is not where its content says
    it belongs becomes a ``Problem`` and the records beside it are still returned:
    one bad file in a directory two processes write to is an ordinary event, and
    a reader that stopped at it would take every open request down with it.

    Nothing waits, either, on what a party put in its own directory. A name that is
    a FIFO, a device, a link or a directory is reported without being read, and so
    is a file over ``MAX_RECORD_BYTES``; a directory with more than ``MAX_ENTRIES``
    names is reported and skipped whole. Every write function reads first, so a read
    one party could stall is a channel one party could stop.

    A directory that was not read is in ``unread`` as well as in ``problems``, and a
    root that was not read puts ``EVERY_PARTY`` there. A root that does not exist
    was read: there is nothing in it.

    Dot-names are skipped without a report. They are writes in flight, and
    reporting one would raise a problem on every read that overlapped a write.

    Sorted, so the result does not depend on the order a directory lists in.
    """
    try:
        parties = sorted(_listing(root))
    except FileNotFoundError:
        return Reading((), (), frozenset())
    except OSError as exc:
        return Reading(
            (), (Problem("", ProblemKind.UNREADABLE, str(exc)),), frozenset({EVERY_PARTY})
        )
    if len(parties) > MAX_ENTRIES:
        return Reading(
            (), (Problem("", ProblemKind.UNREADABLE, _TOO_MANY),), frozenset({EVERY_PARTY})
        )

    records: list[Record] = []
    problems: list[Problem] = []
    unread: set[str] = set()
    for party in parties:
        if party.startswith("."):
            continue
        try:
            names = sorted(_listing(root / party))
        except NotADirectoryError:
            problems.append(Problem(party, ProblemKind.FOREIGN, "a file where a party belongs"))
            continue
        except OSError as exc:
            problems.append(Problem(party, ProblemKind.UNREADABLE, str(exc)))
            unread.add(party)
            continue
        if address_refusal(party) is not None:
            problems.append(Problem(party, ProblemKind.FOREIGN, "not a usable address"))
            continue
        if len(names) > MAX_ENTRIES:
            problems.append(Problem(party, ProblemKind.UNREADABLE, _TOO_MANY))
            unread.add(party)
            continue
        for name in names:
            if name.startswith("."):
                continue
            found = _read_one(root, party, name)
            if isinstance(found, Problem):
                problems.append(found)
            else:
                records.append(found)
    return Reading(tuple(records), tuple(problems), frozenset(unread))


def derive(
    records: Iterable[Record],
    *,
    holds: HoldRule,
    operators: frozenset[str],
    unread: frozenset[str],
) -> View:
    """
    The state of every request, as a function of the records and of whose
    directory they could not be taken from.

    Pure: no clock, no IO, and the same answer for the same records in any order.
    Nothing compares two parties' timestamps, so the result does not depend on
    their clocks agreeing either.

    A request is ``CLOSED`` when its asker has a close for it, ``ANSWERED`` when its
    addressee has an answer for it, and ``OPEN`` otherwise. Held answers count: the
    state says what is on disk, and each item's ``delivery`` says separately whether
    it has reached anybody.

    The rules the write functions check are checked again here, because a file on
    disk did not necessarily come through them. A close by somebody other than the
    asker, an answer by somebody other than the addressee and a release by a writer
    outside ``operators`` change nothing and are reported, as is any record
    referring to something that is not there.

    So is an answer, a receipt or a release whose record no longer has the content
    it was made against, which is what a party rewriting its own file produces. It
    is checked ahead of who wrote it: an answer to a request whose addressee was
    since changed is a stale answer, and calling it one by a non-addressee would
    blame the party that did nothing.

    ``unread`` is ``Reading.unread``: the parties whose directory was not read. A
    record that refers into one of those is ``UNRESOLVED`` and not an ``ORPHAN`` --
    the request an answer names may be sitting in a directory with too many names
    in it, and that is not the answerer's file being wrong. So is anything done to
    a record set aside that way. The view carries ``unread`` on, because the rest
    of what a missing directory costs cannot be marked on any one record: nothing
    here can tell a request nobody answered from one whose answer was not read, or
    a held record from one whose release was not read. Both come out on the side
    of less having happened.

    ``holds``, ``operators`` and ``unread`` have no defaults. Each would be a
    decision about what crosses between sessions unreviewed, or about what was
    read, taken by whoever forgot the argument.
    """
    records = tuple(records)
    problems: list[Problem] = []

    def not_read(party: str) -> bool:
        return party in unread or EVERY_PARTY in unread

    # Records that could not be placed because of where they point, not what they
    # say. A receipt for one, or a release of one, cannot be placed either.
    unresolved: set[RecordId] = set()
    requests = {r.ref: r for r in records if isinstance(r, Request)}
    # What each addressable record's content is now. Requests first; an answer is
    # added only once it is in a thread.
    content = {r.id: digest(r) for r in requests.values()}

    answers: dict[RequestRef, list[Answer]] = {}
    closes: dict[RequestRef, Close] = {}
    for record in records:
        match record:
            case Answer():
                request = requests.get(record.ref)
                if request is None and not_read(record.ref.asker):
                    unresolved.add(record.id)
                    problems.append(
                        _problem(
                            record,
                            ProblemKind.UNRESOLVED,
                            f"answers a request in {record.ref.asker}, which was not read",
                        )
                    )
                elif request is None:
                    problems.append(_problem(record, ProblemKind.ORPHAN, "answers no request"))
                elif record.answers != content[request.id]:
                    problems.append(
                        _problem(record, ProblemKind.STALE, "answers a request since changed")
                    )
                elif request.to != record.answerer:
                    problems.append(
                        _problem(record, ProblemKind.UNENTITLED, "answer by a non-addressee")
                    )
                else:
                    answers.setdefault(record.ref, []).append(record)
            case Close():
                if record.ref.asker != record.asker:
                    problems.append(
                        _problem(record, ProblemKind.UNENTITLED, "close by a non-asker")
                    )
                elif record.ref not in requests:
                    problems.append(_problem(record, ProblemKind.ORPHAN, "closes no request"))
                else:
                    earlier = closes.get(record.ref)
                    if earlier is None or record.serial < earlier.serial:
                        closes[record.ref] = record
            case Request() | Receipt() | Release():
                pass
            case _:
                assert_never(record)

    content.update((a.id, digest(a)) for group in answers.values() for a in group)

    shown_to: dict[RecordId, set[str]] = {}
    released: set[RecordId] = set()
    for record in records:
        match record:
            case Receipt():
                if record.shown not in content and (
                    record.shown in unresolved or not_read(record.shown.party)
                ):
                    problems.append(
                        _problem(
                            record,
                            ProblemKind.UNRESOLVED,
                            "receipt for a record that could not be placed: "
                            "a directory was not read",
                        )
                    )
                elif record.shown not in content:
                    problems.append(_problem(record, ProblemKind.ORPHAN, "receipt for no record"))
                elif record.digest != content[record.shown]:
                    problems.append(
                        _problem(record, ProblemKind.STALE, "receipt for a record since changed")
                    )
                else:
                    shown_to.setdefault(record.shown, set()).add(record.reader)
            case Release():
                if record.operator not in operators:
                    problems.append(
                        _problem(record, ProblemKind.UNENTITLED, "release by a non-operator")
                    )
                elif record.released not in content and (
                    record.released in unresolved or not_read(record.released.party)
                ):
                    problems.append(
                        _problem(
                            record,
                            ProblemKind.UNRESOLVED,
                            "releases a record that could not be placed: "
                            "a directory was not read",
                        )
                    )
                elif record.released not in content:
                    problems.append(_problem(record, ProblemKind.ORPHAN, "releases no record"))
                elif record.digest != content[record.released]:
                    problems.append(
                        _problem(record, ProblemKind.STALE, "releases a record since changed")
                    )
                else:
                    released.add(record.released)
            case Request() | Answer() | Close():
                pass
            case _:
                assert_never(record)

    def item(record: Request | Answer) -> Item:
        if not holds(record):
            delivery = Delivery.DELIVERED
        elif record.id in released:
            delivery = Delivery.RELEASED
        else:
            delivery = Delivery.HELD
        return Item(record, delivery, tuple(sorted(shown_to.get(record.id, ()))))

    threads = []
    for ref in sorted(requests):
        request = requests[ref]
        answered = sorted(answers.get(ref, ()), key=lambda a: a.serial)
        close = closes.get(ref)
        if close is not None:
            state = State.CLOSED
        elif answered:
            state = State.ANSWERED
        else:
            state = State.OPEN
        threads.append(
            Thread(
                request=request,
                state=state,
                items=(item(request), *(item(a) for a in answered)),
                close=close,
            )
        )
    return View(
        threads=tuple(threads),
        problems=tuple(sorted(problems, key=lambda p: (p.source, p.kind.value, p.detail))),
        unread=tuple(sorted(unread)),
    )


def pending_for(view: View, party: str) -> tuple[Item, ...]:
    """
    What is for ``party``, has reached it, and it holds no receipt for.

    A request is for its addressee and an answer is for the asker of the request it
    answers. Held items are left out -- they are ``held_for``'s -- and so is
    everything on a closed request: the asker has finished with it, and showing
    either side its unread remainder asks for work nobody is waiting on.
    """
    return _for(view, party, held=False)


def held_for(view: View, party: str) -> tuple[Item, ...]:
    """
    What is for ``party`` and is waiting on an operator's release.
    """
    return _for(view, party, held=True)


def _for(view: View, party: str, *, held: bool) -> tuple[Item, ...]:
    out = []
    for thread in view.threads:
        if thread.state is State.CLOSED:
            continue
        for item in thread.items:
            match item.record:
                case Request():
                    recipient = item.record.to
                case Answer():
                    recipient = thread.request.asker
                case _:
                    assert_never(item.record)
            if recipient != party or party in item.shown_to:
                continue
            if (item.delivery is Delivery.HELD) is held:
                out.append(item)
    return tuple(out)


def _request(records: tuple[Record, ...], ref: RequestRef) -> Request | None:
    return next((r for r in records if isinstance(r, Request) and r.ref == ref), None)


def _closed(records: tuple[Record, ...]) -> frozenset[RequestRef]:
    """
    Requests with a close by their own asker.
    """
    asked = {r.ref for r in records if isinstance(r, Request)}
    return frozenset(
        r.ref for r in records if isinstance(r, Close) and r.ref.asker == r.asker and r.ref in asked
    )


def _addressable(records: tuple[Record, ...]) -> dict[RecordId, str]:
    """
    What a receipt or a release may name, and the content each has now: requests,
    and answers by their addressee to the request as it stands.
    """
    requests = {r.ref: r for r in records if isinstance(r, Request)}
    out = {r.id: digest(r) for r in requests.values()}
    for record in records:
        if isinstance(record, Answer):
            request = requests.get(record.ref)
            if (
                request is not None
                and request.to == record.answerer
                and record.answers == out[request.id]
            ):
                out[record.id] = digest(record)
    return out


def _seen_refusal(records: tuple[Record, ...], seen: Request | Answer) -> Refusal | None:
    """
    Why a receipt for or a release of ``seen`` may not be written, or None.
    """
    current = _addressable(records).get(seen.id)
    if current is None:
        return Refusal.NO_SUCH_RECORD
    if current != digest(seen):
        return Refusal.CHANGED
    return None


def _writer(record: Record) -> str:
    match record:
        case Request():
            return record.asker
        case Answer():
            return record.answerer
        case Close():
            return record.asker
        case Receipt():
            return record.reader
        case Release():
            return record.operator
        case _:
            assert_never(record)


def _problem(record: Record, kind: ProblemKind, detail: str) -> Problem:
    return Problem(f"{record.id.party}/{record.id.name}.json", kind, detail)


def _stem(series: str, serial: int) -> str:
    return f"{series}-{serial:0{_WIDTH}d}"


_TOO_MANY = f"more than the {MAX_ENTRIES} entries a read takes"


def _listing(directory: Path) -> list[str]:
    """
    The names in a directory, and never more than one past ``MAX_ENTRIES`` of them:
    enough for the caller to tell that there were too many, in memory that does not
    grow with what the directory's owner put there.
    """
    with os.scandir(directory) as entries:
        return [entry.name for entry in itertools.islice(entries, MAX_ENTRIES + 1)]


def _next_serial(directory: Path, series: str) -> int:
    """
    One past the highest number issued in a series, from the names alone.

    No file is opened. A number's only memory is the name that took it, which is
    what makes the count survive a record that cannot be read -- and is also its
    limit: nothing here deletes a record, and a series whose last file is removed
    by something else issues that number again. What was written against the old
    record does not move to the new one: see ``digest``.

    Every name of the series' shape counts, one a read reports as ``FOREIGN`` for
    its padding included: ``request-41.json`` holds 41. Refusing to reissue a number
    somebody may have seen is the safe direction, and the only directory a name
    can be put in to move a party's numbering is that party's own.

    In a directory past ``MAX_ENTRIES`` the listing is cut short and this can come
    out low. ``_append`` does not write there, so that is ``next_number``'s to say
    of a directory no read takes records from.
    """
    try:
        names = _listing(directory)
    except OSError:
        return 1
    highest = 0
    for name in names:
        match = _NAME.fullmatch(name)
        if match is not None and match.group(1) == series:
            highest = max(highest, int(match.group(2)))
    return highest + 1


def _append(directory: Path, series: str, make: Callable[[int], _R]) -> _R:
    """
    Write one record under the next free number in a series. Returns it.

    ``make`` is called with each number tried, because the record carries its own
    number and a collision moves it. The link is the allocation: the name exists
    only once the content is complete, and it fails rather than replaces when
    another writer took the name first.

    **Returns the record as it decodes from the text written**, not the one
    ``make`` built. The two differ wherever a value has more than one spelling the
    encoder accepts -- an integer where a float is declared is the one the type
    checker lets through -- and a digest is taken over the spelling. Raises
    ``ValueError`` for a record that would not decode at all.

    Raises ``_Unwritable``, having written nothing, for a record no read would
    take: one over the size, or one into a directory -- or under a root -- that
    would then hold more names than a read lists. The temp file and the numbered
    name both exist for a moment, so the room asked for is two names, and a
    writer alone in its directory never puts it past the limit even mid-write.
    The count and the write are not one step: a second writer at the same address,
    or anything else dropped in the directory between them, can still fill it.
    """
    try:
        parties = _listing(directory.parent)
    except FileNotFoundError:
        parties = []
    if len(parties) + (directory.name not in parties) > MAX_ENTRIES:
        raise _Unwritable(Refusal.FULL)
    directory.mkdir(parents=True, exist_ok=True)
    if len(_listing(directory)) + 2 > MAX_ENTRIES:
        raise _Unwritable(Refusal.FULL)
    serial = _next_serial(directory, series)
    while True:
        # ASCII with escapes, so any `str` is writable -- a lone surrogate in quoted
        # text included -- and reads back as the same `str`. It also makes the
        # length of the text the size of the file.
        text = json.dumps(_encode(make(serial)), ensure_ascii=True, indent=1, sort_keys=True) + "\n"
        if len(text) > MAX_RECORD_BYTES:
            raise _Unwritable(Refusal.TOO_LARGE)
        record = cast(_R, _decode(json.loads(text)))
        fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".writing-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(text)
                # On the device before it has a name. Without this a power loss can
                # leave a numbered name over a short file: the number is spent, the
                # request is gone, and its asker was told it was posted.
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp, directory / f"{_stem(series, serial)}.json")
            except FileExistsError:
                serial += 1
                continue
            return record
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)


def _encode(record: Record) -> dict[str, Any]:
    match record:
        case Request():
            return {
                "kind": "request",
                "asker": record.asker,
                "number": record.number,
                "to": record.to,
                "subject": record.subject,
                "body": record.body,
                "basis": record.basis.value,
                "blocks": record.blocks,
                "created": record.created,
                "sender_policy": record.sender_policy,
                "sender_contained": record.sender_contained,
                "quotes": _encode_quotes(record.quotes),
            }
        case Answer():
            return {
                "kind": "answer",
                "answerer": record.answerer,
                "serial": record.serial,
                "ref": _encode_ref(record.ref),
                "answers": record.answers,
                "body": record.body,
                "basis": record.basis.value,
                "at": record.at,
                "sender_policy": record.sender_policy,
                "sender_contained": record.sender_contained,
                "quotes": _encode_quotes(record.quotes),
            }
        case Close():
            return {
                "kind": "close",
                "asker": record.asker,
                "serial": record.serial,
                "ref": _encode_ref(record.ref),
                "at": record.at,
            }
        case Receipt():
            return {
                "kind": "receipt",
                "reader": record.reader,
                "serial": record.serial,
                "shown": _encode_id(record.shown),
                "digest": record.digest,
                "at": record.at,
            }
        case Release():
            return {
                "kind": "release",
                "operator": record.operator,
                "serial": record.serial,
                "released": _encode_id(record.released),
                "digest": record.digest,
                "at": record.at,
            }
        case _:
            assert_never(record)


def _encode_ref(ref: RequestRef) -> dict[str, Any]:
    return {"asker": ref.asker, "number": ref.number}


def _encode_id(record_id: RecordId) -> dict[str, Any]:
    return {"party": record_id.party, "name": record_id.name}


def _encode_quotes(quotes: tuple[Quote, ...]) -> list[dict[str, Any]]:
    return [{"text": q.text, "source": q.source} for q in quotes]


class _NotARecord(ValueError):
    pass


class _Unwritable(Exception):
    """A record ``_append`` did not write, and the refusal its caller returns."""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


def _written(directory: Path, series: str, make: Callable[[int], _R]) -> _R | Refusal:
    try:
        return _append(directory, series, make)
    except _Unwritable as exc:
        return exc.refusal


# What one ``os.read`` asks for. The call allocates what it asks for before it knows
# how much is there, and nearly every record is a few hundred bytes.
_CHUNK = 1 << 16


def _contents(path: Path) -> bytes | str:
    """
    The bytes of a regular file no larger than a record may be, or why it was not
    read. Raises ``OSError`` as ``open`` does.

    The file is judged through the descriptor and not by a ``stat`` of the name
    beforehand: the name belongs to another party, which can swap what it points at
    between a check and an open. ``O_NONBLOCK`` is what makes opening a FIFO return,
    and ``O_NOFOLLOW`` refuses a link outright -- a record is never one, since the
    writer links a regular file into place. The read stops one byte past the limit,
    so a file that grows under it costs no more than one that was already too big.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            return "not a regular file"
        chunks: list[bytes] = []
        held = 0
        while held <= MAX_RECORD_BYTES and status.st_size <= MAX_RECORD_BYTES:
            chunk = os.read(fd, min(_CHUNK, MAX_RECORD_BYTES + 1 - held))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            held += len(chunk)
        return f"larger than the {MAX_RECORD_BYTES} bytes a record may be"
    finally:
        os.close(fd)


def _read_one(root: Path, party: str, name: str) -> Record | Problem:
    source = f"{party}/{name}"
    match = _NAME.fullmatch(name)
    if match is None:
        return Problem(source, ProblemKind.FOREIGN, "not a record name")
    series, serial = match.group(1), int(match.group(2))
    if name != f"{_stem(series, serial)}.json":
        # `request-7.json` and `request-0000007.json` would both read as number 7.
        return Problem(source, ProblemKind.FOREIGN, "not a record name")
    try:
        raw = _contents(root / party / name)
    except OSError as exc:
        return Problem(source, ProblemKind.UNREADABLE, str(exc))
    if isinstance(raw, str):
        return Problem(source, ProblemKind.UNREADABLE, raw)
    try:
        record = _decode(json.loads(raw.decode("utf-8")))
    except (ValueError, RecursionError) as exc:
        return Problem(source, ProblemKind.MALFORMED, str(exc))
    if record.id != RecordId(party, _stem(series, serial)):
        return Problem(
            source,
            ProblemKind.MISPLACED,
            f"content is {record.id.party}/{record.id.name}",
        )
    return record


def _decode(data: object) -> Record:
    """
    The record a parsed file holds. Raises ``_NotARecord`` for anything else.

    Keys this does not know are ignored, so a record written by a later version
    with more to say is still a record to this one.
    """
    if not isinstance(data, dict):
        raise _NotARecord("not an object")
    kind = data.get("kind")
    if kind == "request":
        return Request(
            asker=_text(data, "asker"),
            number=_whole(data, "number"),
            to=_text(data, "to"),
            subject=_text(data, "subject"),
            body=_text(data, "body"),
            basis=_basis(data),
            blocks=_text(data, "blocks"),
            created=_clock(data, "created"),
            sender_policy=_policy(data),
            sender_contained=_contained(data),
            quotes=_quotes(data),
        )
    if kind == "answer":
        return Answer(
            answerer=_text(data, "answerer"),
            serial=_whole(data, "serial"),
            ref=_ref(data),
            answers=_text(data, "answers"),
            body=_text(data, "body"),
            basis=_basis(data),
            at=_clock(data, "at"),
            sender_policy=_policy(data),
            sender_contained=_contained(data),
            quotes=_quotes(data),
        )
    if kind == "close":
        return Close(
            asker=_text(data, "asker"),
            serial=_whole(data, "serial"),
            ref=_ref(data),
            at=_clock(data, "at"),
        )
    if kind == "receipt":
        return Receipt(
            reader=_text(data, "reader"),
            serial=_whole(data, "serial"),
            shown=_record_id(data, "shown"),
            digest=_text(data, "digest"),
            at=_clock(data, "at"),
        )
    if kind == "release":
        return Release(
            operator=_text(data, "operator"),
            serial=_whole(data, "serial"),
            released=_record_id(data, "released"),
            digest=_text(data, "digest"),
            at=_clock(data, "at"),
        )
    raise _NotARecord(f"unknown kind {kind!r}")


def _text(data: dict[Any, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise _NotARecord(f"{key!r} is not text")
    return value


def _basis(data: dict[Any, Any]) -> Basis:
    try:
        return Basis(_text(data, "basis"))
    except ValueError:
        raise _NotARecord("'basis' is not one this version knows") from None


def _whole(data: dict[Any, Any], key: str) -> int:
    value = data.get(key)
    # `True` is an `int` to isinstance, and is not a number anybody issued.
    if type(value) is not int or value < 1:
        raise _NotARecord(f"{key!r} is not a positive integer")
    return value


def _clock(data: dict[Any, Any], key: str) -> float:
    value = data.get(key)
    # `True` is an `int` to isinstance here too.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _NotARecord(f"{key!r} is not a time")
    try:
        return float(value)
    except OverflowError:
        # An integer JSON can spell and a float cannot hold.
        raise _NotARecord(f"{key!r} is not a time") from None


def _policy(data: dict[Any, Any]) -> str | None:
    value = data.get("sender_policy")
    if value is not None and not isinstance(value, str):
        raise _NotARecord("'sender_policy' is neither text nor null")
    return value


def _contained(data: dict[Any, Any]) -> bool | None:
    value = data.get("sender_contained")
    if value is not None and not isinstance(value, bool):
        raise _NotARecord("'sender_contained' is neither a boolean nor null")
    return value


def _inner(data: dict[Any, Any], key: str) -> dict[Any, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise _NotARecord(f"{key!r} is not an object")
    return value


def _ref(data: dict[Any, Any]) -> RequestRef:
    inner = _inner(data, "ref")
    return RequestRef(_text(inner, "asker"), _whole(inner, "number"))


def _record_id(data: dict[Any, Any], key: str) -> RecordId:
    inner = _inner(data, key)
    return RecordId(_text(inner, "party"), _text(inner, "name"))


def _quotes(data: dict[Any, Any]) -> tuple[Quote, ...]:
    value = data.get("quotes")
    if not isinstance(value, list):
        raise _NotARecord("'quotes' is not a list")
    out = []
    for entry in value:
        if not isinstance(entry, dict):
            raise _NotARecord("a quote is not an object")
        out.append(Quote(_text(entry, "text"), _text(entry, "source")))
    return tuple(out)
