"""
A board's facts on disk: one record per file, appended, restored by a fold.

**The log holds applied outcomes, not requests.** The 08-14 record says *persist the
intent stream, derive board state from it on load*, and the live intents are not
that stream: fed back through ``store._apply`` they are questions asked of a world
that is no longer there. A declaration is refused as a duplicate when another
session's lead already used the id, because ``tasks`` is one map for the fleet. A
bare claim re-picks instead of restoring who won. An inbox read that was not logged
re-delivers the session's mail. ``Task.writes`` is built from a parked approval no
later snapshot holds. Every timestamp is a dead process's ``time.monotonic()``. So
a record here is a fact as the reducer settled it -- *this task was claimed by this
agent* -- and restoring is a fold over facts that cannot be refused.

**One file per record, no index.** ``brief.py`` makes the argument and it transfers
whole: a write above ``PIPE_BUF`` is not atomic, a document rewritten per event is a
corruption path, and the directory listing is the consistent prefix. What does not
transfer is ``brief.write_entry``. It reads every entry to find the next ordinal,
it has three digits, and it guards with ``exists()`` before ``os.replace`` -- a
check-then-act that a second writer wins by destroying a record with nothing
raised. Two windows resuming one session id are two writers, so a record here is
*linked* into place: ``os.link`` refuses an existing name atomically, and the
writer that lost takes the next ordinal. Every append lists the directory first
and starts past the highest name there, because a writer told where to start can be
sent into the place of a missing record, and a record read back ahead of ones
written before it is a fact restored out of order.

**Two writers lose no record, and their records are not one history.** Each window
has its own store, so each reducer grants what the other would refuse, and a record
does not say which window wrote it. ``restore`` therefore applies a record only
where one reducer could have appended it after the ones before: from the first that
could not have been, the task or concern is ``contested``, stands as it stood, and
every later record about it is kept unapplied. What that cannot see is two writers
whose records are each allowed in turn -- one window amends, the other, still
holding the old text, claims and finishes -- and such a task restores as one
history with nothing marking it. Closing that needs the writer in the record.

**The directory is a parameter and its key is the session id.** Beside the brief
does not work: a solo session has no brief, a fork inherits its parent's brief
directory under a new id, and the brief's slug moves with the working directory.
Which root these live under is not decided here.

**Who is an address and, beside it, the node.** ``by`` is what the board showed --
``builder-2`` -- and ``by_id`` is the ``NodeId`` it showed it for: one string for a
session's root, the session and the CLI's agent id for a sub-agent. The address
alone is not an identity. Addresses are handed out in spawn order by each process,
so the second process to run a session gives ``builder-2`` to whichever builder it
starts second, and a log keyed on the name would make that agent the holder of
work it never claimed. Whether the node itself outlives the process is not known
here: the CLI's documentation says a resumed session can wake a sub-agent under
the id it had, nothing in this repository has measured it across a restart, and
the format does not depend on the answer. A party restores as its ``NodeId`` when
the record has one and as the bare address when it does not -- a record written
before the id was, or by hand -- and the two never compare equal, so a name is
never promoted to an agent. A node the board had no name for is written with an
empty address and its id: two of those are two parties.

**The format only grows.** A field is added with a default that claims nothing
and a fact is added as a new kind; nothing is renamed, retyped or removed. A
build older than a kind reads its records as ``Unrecognised``, which makes the
board ``short`` and is not damage, and a build older than a field ignores the key.

**A task that adds a board fact adds a record kind**: a frozen dataclass with a
``KIND``, a member of ``Record``, an arm in ``restore``, and a case in
``boardfacts.outcomes`` that derives it. mypy points at the fold when the arm is
missing, and ``tests/test_boardlog.py`` fails when its generator does not produce
the new kind. A fact with no record kind persists nothing and
every existing test still passes, which is why the test counts kinds. Two are
expected and deliberately not guessed at: a decision raised and answered, and a
reference to an inter-session request. Until this build defines them, a record of
either kind is carried as ``Unrecognised`` rather than dropped or treated as damage.

**One kind is not a board fact.** ``RecordingStopped`` is the writer's statement
about the log itself -- records stopped being appended here, and why -- so no
intent derives it and it is a member of ``Record`` beside ``Known`` and not of it.
It is the difference between a log that ends because the board did and one that
ends because writing did, which the records before it cannot say.

**A field added to ``model.Task`` or ``model.Concern`` is a board fact too**, and
it needs no new kind to be lost: it rides an existing one or it is not written.
Nothing in the type system connects the two, so ``tests/test_boardlog.py`` walks
the fields of both records and fails on one that no record restores and no list
there names as deliberately left out. A field added to a record carries a default
that claims nothing, because a log written before the field existed has no key
for it and reads as that default.

**A reader's digest mark is not here.** ``AgentRecord.digest_read_at`` is a cursor
on a node, not a fact about a task or a concern, and the node is the thing a
restore cannot bring back. Losing it also fails the safe way, which is what
separates it from an inbox delivery mark: an unmarked concern is handed over a
second time as though it were new, while an unmarked reader is given a digest that
counts from the session's start and says so.

**Nothing here is in the snapshot, and nothing here reads a clock.** Appending and
reading are IO for the shell; ``restore`` is pure and returns a value. How that
value enters the store belongs to whoever wires this.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import math
import os
import re
import stat
import tempfile
import types
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, assert_never, get_args, get_type_hints

from .model import ApprovedWrites, ConcernState, NodeId, TaskState, closing_result

# `00000042.json`. The name is the ordinal and nothing else: a kind in the name
# would let two writers link two different names for one ordinal, and exclusivity
# is a property of the name. Eight digits because a long session passes a thousand
# records -- three per task, two per concern -- and zero-padding keeps `ls` in the
# order the reader uses. A log that outgrows the width still orders correctly,
# since the reader sorts by value.
_ORDINAL_WIDTH = 8
# Exactly how ``_name`` spells an ordinal, and ASCII digits only: `\d` would take
# digits of other scripts, which ``int`` reads and no append writes.
_RECORD = re.compile(r"^([0-9]{8}|[1-9][0-9]{8,})\.json$")

# The most one record's file may hold. ``append`` refuses a record whose text is
# longer and ``read`` takes no more than this from a file, so what was appended can
# always be read back and a file of any size under a record's name costs a reader
# this much memory and no more. ``requests.MAX_RECORD_BYTES`` is the same figure
# for the same reason.
MAX_RECORD_BYTES = 1 << 20

# Text at the boundary: an address, a basis, an id. Every field of every record is
# one of these shapes, which is what lets one codec serve all of them.
_FIELD_TYPES: tuple[Any, ...] = (str, str | None, float, int, bool, tuple[str, ...])


@dataclass(frozen=True, slots=True)
class Declared:
    """
    A task as it landed on the board.

    ``depends_on`` includes the dependencies the store added for overlapping
    ``touches``, and ``touches`` is the normalised form: what the reducer stored,
    not what the declarer sent.
    """

    KIND: ClassVar[str] = "task-declared"

    at: float
    task_id: str
    title: str
    declared_by: str
    detail: str = ""
    depends_on: tuple[str, ...] = ()
    touches: tuple[str, ...] = ()
    # How the declarer says it knows what the text asserts: a ``model.Basis`` value,
    # or None for unstated. Text rather than the enum, here and on every record,
    # because a member this build does not know would otherwise make the record
    # unreadable and end the log at it.
    basis: str | None = None
    # The one property the work may not break, on one line as the reducer stored
    # it. Empty is "none was stated", which is also what a record with no such key
    # reads as.
    property: str = ""
    # The declarer's node. On every record a party is two fields, the address and
    # this: one string is the root of that session, two are a session and the
    # agent id of one of its sub-agents, and none is "not recorded".
    declared_by_id: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Claimed:
    """
    A claim the store granted. A claim that found nothing is not a fact.

    The version the claimer was handed is not a field: it is the one the task had
    when this record was appended, which the order of the log already says.
    """

    KIND: ClassVar[str] = "task-claimed"

    at: float
    task_id: str
    by: str
    by_id: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Released:
    """
    A claimer gave the task back and the store accepted it.

    ``at`` is the whole of ``Task.released_at``: the time is the record's own.
    """

    KIND: ClassVar[str] = "task-released"

    at: float
    task_id: str
    by: str
    by_id: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Reclaimed:
    """
    A claim was given up on its holder's behalf: ``by`` put a task ``holder`` was
    holding back in the pool.

    Its own kind and not a ``Released`` naming the holder, which would record the
    holder doing something it did not do. This is also the record that ends the
    contest a restart leaves behind (``Board``): after it the task is pending, so
    the next process's claim is one a single reducer could have appended.

    ``by`` joins ``released_by`` and ``holder`` does not. ``released_by`` is the
    set a bare claim skips, and the agent that just handed a task back to the
    pool is the one a bare claim would otherwise hand it straight to; the holder
    declined nothing, and if it runs again it can take the work back.
    """

    KIND: ClassVar[str] = "task-reclaimed"

    at: float
    task_id: str
    by: str
    holder: str
    by_id: tuple[str, ...] = ()
    holder_id: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Handed:
    """
    The agent holding a task was shown ``version`` of its specification after it
    claimed. A completion is a result of the version its author was last handed.
    """

    KIND: ClassVar[str] = "task-version-handed"

    at: float
    task_id: str
    by: str
    version: int
    by_id: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Completed:
    """A claimer finished, with its account of the work."""

    KIND: ClassVar[str] = "task-completed"

    at: float
    task_id: str
    by: str
    result: str = ""
    basis: str | None = None
    # What the claimer says the completion did not execute, in the three values
    # ``model.Task.not_run`` keeps apart: None for unsaid, text for what was left
    # unrun, and the empty string for the statement that nothing was. A record
    # with no such key reads as None, so a completion logged before this was
    # recorded restores as unsaid and never as "everything was run".
    not_run: str | None = None
    by_id: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Closed:
    """
    The operator closed a task (``intents.TaskClosed``): it is finished because
    the operator said to stop waiting for it, and no agent's work is reported.

    Its own kind and not a ``Completed``. A completion is a claimer's and is one
    a reducer could have appended only for the party holding the task, which a
    closed task need not have and the operator is not; and a ``Completed`` that
    carried the closing words would be indistinguishable from an agent that
    typed them into its result.

    ``reason`` is the operator's text as typed. The result a board shows is
    composed from it where the reducer composes it (``model.closing_result``).
    """

    KIND: ClassVar[str] = "task-closed"

    at: float
    task_id: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Amended:
    """
    A task's specification was amended.

    ``detail`` and ``basis`` are the specification as the amendment left it, and
    so is ``property`` when it is text. It is a version when the detail or the
    property differs from what stood; an amendment that repeats both still
    empties ``released_by`` and adds none.

    ``by`` is None, and ``by_id`` empty, for the operator, the way
    ``intents.TaskAmended.node_id`` is.
    """

    KIND: ClassVar[str] = "task-amended"

    at: float
    task_id: str
    detail: str
    by: str | None = None
    # The property the task is held to once this amendment is applied, on one
    # line: text is that property and the empty string is "none stands". None is
    # "this record does not say", which is what a record with no such key reads
    # as, and ``restore`` then leaves the standing property in force.
    property: str | None = None
    # The standing of the text this amendment left, as the reducer settled it:
    # the amender's own word for a text it replaced, and the standing one's for a
    # text it left alone.
    basis: str | None = None
    by_id: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WritesAccrued:
    """
    One approved write measured against the task that was open, as a delta.

    The addend exists only in the reducer arm that resolves the approval
    (``model.ApprovedWrites``), so it is logged or it is gone.
    """

    KIND: ClassVar[str] = "task-writes-accrued"

    at: float
    task_id: str
    paths: tuple[str, ...] = ()
    unplaced: tuple[str, ...] = ()
    lines_added: int = 0
    lines_removed: int = 0


@dataclass(frozen=True, slots=True)
class Posted:
    """A concern as it was filed."""

    KIND: ClassVar[str] = "concern-posted"

    at: float
    concern_id: str
    sender: str
    recipient: str
    subject: str
    body: str
    task_id: str | None = None
    basis: str | None = None
    # The sender marked this as a report of something that went wrong. False is
    # "not marked", as on ``model.Concern.incident``, which is not "nothing went
    # wrong" and is what a record with no such key reads as.
    incident: bool = False
    sender_id: tuple[str, ...] = ()
    recipient_id: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Edited:
    """The operator rewrote a concern before it was delivered."""

    KIND: ClassVar[str] = "concern-edited"

    at: float
    concern_id: str
    body: str


@dataclass(frozen=True, slots=True)
class Withdrawn:
    """The operator stopped a concern from being delivered."""

    KIND: ClassVar[str] = "concern-withdrawn"

    at: float
    concern_id: str


@dataclass(frozen=True, slots=True)
class Delivered:
    """
    One inbox read's worth of concerns, handed over.

    The ids rather than the request: "everything waiting at that point" is a
    question whose answer depends on every record before it, and this is the
    answer. An inbox read that found nothing delivers nothing and writes nothing.
    """

    KIND: ClassVar[str] = "concerns-delivered"

    at: float
    concern_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecordingStopped:
    """
    The writer of this log stopped appending to it here: a record after the ones
    before this did not land, and nothing was appended behind the hole.

    Not a fact about a task or a concern. It is what makes the records before it
    read as a prefix rather than as the board: without it a log that ends because
    a completion could not be written is indistinguishable from one where the
    task was never finished. ``restore`` lists it in ``Board.stopped``, which
    makes the board ``short``, and applies whatever follows it as it would
    anywhere -- a later process that restored the prefix and carried on is one
    history from that prefix, and the board it leaves is still short one fact or
    more, for good.

    A build older than this kind reads it as ``Unrecognised``, which is also
    ``short``.

    ``reason`` is the writer's words for why the record did not land, cut by the
    writer to a length that leaves this record far under ``MAX_RECORD_BYTES``:
    the record that says a record was too long must not be.
    """

    KIND: ClassVar[str] = "recording-stopped"

    at: float
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Unrecognised:
    """
    A well-formed record of a kind this build does not define.

    Carried rather than refused. A log is read strictly in order, so treating a
    newer build's record as damage would discard every record after it, and
    skipping it silently would restore a board short one fact with nothing saying
    so. ``Board.unrecognised`` is where it surfaces.

    Built only by ``decode``. ``text`` is the record's JSON as it was read, and
    encoding writes it back unchanged.
    """

    at: float
    kind: str
    text: str


# The closed vocabulary of board facts: what a reducer step can yield. A decision
# raised or answered, and a reference to an inter-session request, join here when
# the tasks that define them land.
Known = (
    Declared
    | Claimed
    | Released
    | Reclaimed
    | Handed
    | Completed
    | Closed
    | Amended
    | WritesAccrued
    | Posted
    | Edited
    | Withdrawn
    | Delivered
)
Record = Known | RecordingStopped | Unrecognised

_KINDS: dict[str, type[Any]] = {cls.KIND: cls for cls in (*get_args(Known), RecordingStopped)}
assert len(_KINDS) == len(get_args(Known)) + 1, "two record kinds share a name"
_HINTS: dict[type[Any], dict[str, Any]] = {cls: get_type_hints(cls) for cls in _KINDS.values()}
for _cls, _hints in _HINTS.items():
    for _field in dataclasses.fields(_cls):
        # A field of a shape the codec does not know would encode and then fail
        # every read, so it stops the import instead.
        assert _hints[_field.name] in _FIELD_TYPES, (_cls.__name__, _field.name)


# -- one record as text -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Malformed:
    """Why a file's text is not a record."""

    reason: str


def encode(record: Record) -> str:
    """
    One record as the text of its file: a JSON object and a newline.

    The newline is the terminator, not decoration. Without it the text one byte
    short of a whole record would itself parse, and with it no proper prefix of an
    encoded record decodes -- which is what lets a reader tell a torn file from a
    whole one without a length or a checksum.

    Escaped to ASCII. Agent prose can carry a lone surrogate, which UTF-8 refuses
    to encode and JSON's escapes carry unchanged.

    Raises ``ValueError`` for a time that is not finite; ``append`` turns that into
    a value.
    """
    if isinstance(record, Unrecognised):
        return record.text + "\n"
    payload: dict[str, Any] = {"kind": record.KIND}
    for field in dataclasses.fields(record):
        value = getattr(record, field.name)
        payload[field.name] = list(value) if isinstance(value, tuple) else value
    return json.dumps(payload, allow_nan=False) + "\n"


def decode(text: str) -> Record | Malformed:
    """
    The record a file holds, or why it holds none.

    A field the record has and the text lacks takes the record's default when it
    has one, so a log written before a field existed still reads. A key the record
    does not have is ignored, so a log written after one was added still reads in
    the build before it. Neither applies to ``kind`` and ``at``, which every record
    has.
    """
    if not text.endswith("\n"):
        return Malformed("no terminator: the write did not finish")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return Malformed(f"not JSON: {exc}")
    except RecursionError:
        # Not a ValueError: brackets nested past the interpreter's own depth.
        return Malformed("not JSON: nested too deeply to parse")
    if not isinstance(payload, dict):
        return Malformed("not a JSON object")
    kind = payload.get("kind")
    if not isinstance(kind, str):
        return Malformed("no kind")
    at = _as(payload.get("at"), float)
    if isinstance(at, Malformed):
        return Malformed(f"at: {at.reason}")

    cls = _KINDS.get(kind)
    if cls is None:
        return Unrecognised(at=at, kind=kind, text=text[:-1])

    values: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        if field.name not in payload:
            if field.default is dataclasses.MISSING:
                return Malformed(f"{kind}: no {field.name}")
            continue
        value = _as(payload[field.name], _HINTS[cls][field.name])
        if isinstance(value, Malformed):
            return Malformed(f"{kind}.{field.name}: {value.reason}")
        values[field.name] = value
    record: Record = cls(**values)
    return record


def _as(value: Any, hint: Any) -> Any:
    """
    ``value`` as the field type ``hint``, or ``Malformed``.

    Strict where JSON is loose: ``true`` is an ``int`` to Python and is not a line
    count, ``1`` is not a mark somebody set, and a time that is not finite orders
    against nothing.
    """
    if hint is bool:
        return value if isinstance(value, bool) else Malformed("not true or false")
    if hint is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return Malformed("not a number")
        try:
            number = float(value)
        except OverflowError:
            # JSON has no width for an integer and a float does.
            return Malformed("too large to be a time")
        return number if math.isfinite(number) else Malformed("not finite")
    if hint is int:
        if isinstance(value, bool) or not isinstance(value, int):
            return Malformed("not an integer")
        return value
    if hint is str:
        return value if isinstance(value, str) else Malformed("not text")
    if isinstance(hint, types.UnionType):
        return value if value is None or isinstance(value, str) else Malformed("not text or null")
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return Malformed("not a list of text")
    return tuple(value)


# -- the directory ----------------------------------------------------------------


# The longest name most filesystems take for one directory entry, in bytes.
_MAX_COMPONENT = 255


def session_dir(root: Path, session_id: str) -> Path | None:
    """
    Where one session's board log lives under ``root``, or None for an id that is
    not one directory name.

    The session id is the whole key. Nothing about the working directory or the
    brief enters it, because a resume from another directory and a fork that
    inherited a brief are both the cases where those would name the wrong board.

    **None rather than a path that leaves** ``root``. ``root / "../x"`` is beside
    it and ``root / "/etc"`` is ``/etc``, and ``append`` makes the directory it is
    given. An id with a separator or a NUL, an empty one, one that starts with a
    dot -- ``.`` and ``..`` among them -- one with no encoding as a file name, or
    one longer than a name may be, has no directory. A value and not a raise, for
    the reason ``Unwritten`` is one.
    """
    if (
        not session_id
        or session_id.startswith(".")
        or any(c in session_id for c in ("/", "\\", "\0"))
        or (os.altsep is not None and os.altsep in session_id)
    ):
        return None
    try:
        encoded = os.fsencode(session_id)
    except UnicodeEncodeError:
        return None
    if len(encoded) > _MAX_COMPONENT:
        return None
    return root / session_id


def _name(ordinal: int) -> str:
    return f"{ordinal:0{_ORDINAL_WIDTH}d}.json"


def _ordinals(names: Iterable[str]) -> list[int]:
    """
    The ordinals among ``names``, ascending: each name that is exactly how its own
    ordinal is spelled. ``000000099.json`` is nine digits for 99 and is not one.

    The one definition of which names are records, for the writer and the reader
    both. A name only the writer counted would put every later record past a gap,
    and one only the reader took would be a record no append could have made.
    """
    return sorted(
        int(match.group(1)) for name in names if (match := _RECORD.match(name)) is not None
    )


def _first_gap(ordinals: list[int]) -> int | None:
    """The lowest ordinal missing below the highest of ``ordinals``, or None."""
    return next((want for want, have in enumerate(ordinals) if have != want), None)


@dataclass(frozen=True, slots=True)
class Unlisted:
    """
    A directory exists, or may, and could not be listed, and why.

    Not zero. "No record is here" and "nobody could look" are different answers,
    and a writer that took the second for the first would start a log on top of
    one it could not see.
    """

    reason: str


def next_ordinal(directory: Path) -> int | Unlisted:
    """
    The ordinal after the highest one present, from the listing alone.

    Zero for a directory that is not there or holds no record. ``Unlisted`` for
    one that could not be listed for any other reason -- no permission, a file
    where the directory would be, an IO error for one moment -- as ``read``
    keeps the same two apart.

    This is also what a reader keys a memo on. A directory's mtime does not move
    between two appends made close together, so "has anything been added" is
    answered by the names and not by the timestamp.
    """
    try:
        present = _ordinals(os.listdir(directory))
    except FileNotFoundError:
        return 0
    except OSError as exc:
        return Unlisted(f"{type(exc).__name__}: {exc}")
    return present[-1] + 1 if present else 0


@dataclass(frozen=True, slots=True)
class Written:
    """
    A record is on disk.

    ``gap`` **is not None when this record will not be read back.** It is the
    lowest ordinal below this record's with no file, which is where ``read``
    stops, so the record is on disk behind a break and a restore will not reach
    it. The writer is told because nothing else will tell it: no append fills a
    missing ordinal, so the break is permanent and every later append lands
    behind it too.

    It is what the append's own listing shows, without reading a record. It does
    not see a record that is present and does not decode -- a torn write, a
    corrupted file. ``read`` finds those, and None here is not a statement that
    the log is whole.
    """

    ordinal: int
    path: Path
    gap: int | None = None


@dataclass(frozen=True, slots=True)
class Unwritten:
    """
    A record is not on disk, and why.

    A value rather than an exception because the caller is on the frame path, and
    because the operator has to be shown it: a board that silently stopped
    persisting is discovered at the next resume, by its absence.
    """

    reason: str


def append(directory: Path, record: Record, *, durable: bool = True) -> Written | Unwritten:
    """
    Append one record. Never overwrites one, whoever else is writing.

    The text goes to a temp file in the same directory and is then *linked* to its
    ordinal's name. A name therefore appears complete or not at all, and ``os.link``
    fails on a name that exists -- atomically, in the kernel, which is the guarantee
    a check before a rename cannot give. The writer that lost the name takes the
    next one, so two writers interleave and neither loses a record.

    **Never fills a missing ordinal.** The first name tried is one past the highest
    in a listing taken here, and a collision only moves up, so a record never lands
    below one that was there when its append began. That is why there is no way to
    tell this where to start: a caller's idea of the end is below the truth as soon
    as a second writer exists, and the first free name above a stale floor may be
    the place of a record that was lost. Filling it would turn a break ``read``
    reports into a whole-looking log with a newer fact ahead of older ones. The
    price is the listing, which is linear in the session's length on the thread
    that calls this.

    A record whose text is over ``MAX_RECORD_BYTES`` is refused, since ``read``
    would take it for damage and withhold everything after it.

    ``durable`` flushes the record to the device before it is named, which is the
    difference between surviving this process and surviving the machine, and costs
    milliseconds per record on the thread that calls it. Without it a power loss
    can leave a name over a short file; ``read`` reports that as damage and never
    as a record, but it withholds whatever was written after. The directory entry
    is not flushed either way: losing the newest names shortens the log to a
    consistent prefix and tears nothing.

    **A process killed inside this leaves its temp file, and nothing removes it.**
    A ``.record-*.tmp`` name is never a record to ``read`` or to the next
    ordinal, so one left behind costs its bytes and nothing else. Removing them
    is not safe from here: another writer's append in flight is a file of exactly
    that name, and unlinking it before its link would lose that record.
    """
    try:
        data = encode(record).encode("utf-8")
        if len(data) > MAX_RECORD_BYTES:
            return Unwritten(
                reason=f"{len(data)} bytes, over the {MAX_RECORD_BYTES} a record may be"
            )
        directory.mkdir(parents=True, exist_ok=True)
        present = _ordinals(os.listdir(directory))
        ordinal = present[-1] + 1 if present else 0
        gap = _first_gap(present)
        fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".record-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                if durable:
                    handle.flush()
                    os.fsync(handle.fileno())
            while True:
                target = directory / _name(ordinal)
                try:
                    os.link(tmp, target)
                    break
                except FileExistsError:
                    ordinal += 1
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
    except (OSError, ValueError) as exc:
        return Unwritten(reason=f"{type(exc).__name__}: {exc}")
    return Written(ordinal=ordinal, path=target, gap=gap)


@dataclass(frozen=True, slots=True)
class Damage:
    """
    Where a log stops being readable.

    ``unread`` is how many record files sit after the break. Zero is the ordinary
    case -- a write the process did not survive -- and anything else means records
    that were once whole are being withheld, which the operator is owed in those
    words rather than as a shorter board.
    """

    ordinal: int
    reason: str
    unread: int = 0


@dataclass(frozen=True, slots=True)
class Log:
    """What a directory yielded: the readable prefix, and where it ended if early."""

    records: tuple[Record, ...] = ()
    damage: Damage | None = None


def _file_bytes(path: Path) -> bytes:
    """
    The bytes of a regular file no longer than a record may be. ``OSError`` for
    anything else.

    Opened without blocking and checked before it is read: a FIFO under a record's
    name would hold an ordinary open until somebody wrote to it, and a reader that
    waits on a file is one any writer to the directory can stop.

    Read to one byte past ``MAX_RECORD_BYTES`` and no further, so the size of the
    file is never the size of the allocation.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path.name} is not a regular file")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            held = handle.read(MAX_RECORD_BYTES + 1)
        if len(held) > MAX_RECORD_BYTES:
            raise OSError(f"{path.name} is over the {MAX_RECORD_BYTES} bytes a record may be")
        return held
    finally:
        if fd != -1:
            os.close(fd)


def read(directory: Path) -> Log:
    """
    Every record up to the first one that cannot be read, oldest first.

    **Strict about order.** Records are facts about each other -- a claim means
    nothing without its declaration -- so the first ordinal that is absent or does
    not decode ends the log, and nothing after it is applied. What is returned is
    always a prefix of what was written.

    A missing directory is an empty log: a session that has put nothing on its
    board is the common case. A directory that exists and cannot be listed is
    damage, because those two must not read alike.

    Names that are not records are skipped. The directory holds temp files
    mid-write, and a reader that tripped on one would fail exactly while a write
    was in flight.

    **Never raises and never waits**, whatever the files hold: text built to break
    the parser is a record that does not decode, and a name that is not a regular
    file, or is a longer one than ``append`` writes, is one that cannot be read.
    """
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return Log()
    except OSError as exc:
        return Log(damage=Damage(ordinal=0, reason=f"{type(exc).__name__}: {exc}"))

    found = _ordinals(names)
    records: list[Record] = []
    for position, ordinal in enumerate(found):
        unread = len(found) - position
        if ordinal != position:
            return Log(tuple(records), Damage(position, "missing", unread))
        try:
            text = _file_bytes(directory / _name(ordinal)).decode("utf-8")
        except (OSError, ValueError) as exc:
            return Log(tuple(records), Damage(ordinal, f"{type(exc).__name__}: {exc}", unread - 1))
        record = decode(text)
        if isinstance(record, Malformed):
            return Log(tuple(records), Damage(ordinal, record.reason, unread - 1))
        records.append(record)
    return Log(tuple(records))


# -- the board the records describe ------------------------------------------------

# Who a record names, as it restores: the node when the record carries one, the
# bare address when it does not. A ``NodeId`` is a tuple and an address is text,
# so the two kinds are never equal to each other, and two nodes are equal only
# when they are one node -- whatever either was called.
Party = NodeId | str


def _party(address: str, node: tuple[str, ...]) -> Party:
    """
    The party two fields of a record name. A node field of any other length than
    the two a writer produces says nothing, and the address stands alone.
    """
    if len(node) == 1:
        return (node[0], None)
    if len(node) == 2:
        return (node[0], node[1])
    return address


def _named(record: Record) -> tuple[tuple[str | None, tuple[str, ...]], ...]:
    """Every party ``record`` names, as its address and node fields."""
    match record:
        case Declared():
            return ((record.declared_by, record.declared_by_id),)
        case Claimed() | Released() | Handed() | Completed() | Amended():
            return ((record.by, record.by_id),)
        case Reclaimed():
            return ((record.by, record.by_id), (record.holder, record.holder_id))
        case Posted():
            return (
                (record.sender, record.sender_id),
                (record.recipient, record.recipient_id),
            )
        case (
            Closed()
            | WritesAccrued()
            | Edited()
            | Withdrawn()
            | Delivered()
            | RecordingStopped()
            | Unrecognised()
        ):
            # A close is the operator's, who is no party of a board.
            return ()
        case _:
            assert_never(record)


@dataclass(frozen=True, slots=True)
class TaskVersion:
    """
    One text a task has had. ``by`` is None for the operator.

    ``basis`` is here and not on the task because it describes a text: an
    amendment replaces what was asserted, and the declarer's word for how it knew
    the old text says nothing about the new one.

    ``property`` is the one the task was held to while this text stood. An
    amendment that says nothing about it carries the previous version's forward,
    so every version states its own and ``completed_version`` names the property
    a result was written against as well as the text.

    ``model.Task.superseded`` is these without the last, turned round: an entry
    there is a text with who *replaced* it and when, which here is the ``by`` and
    ``at`` of the version after it.
    """

    at: float
    detail: str
    by: Party | None
    basis: str | None = None
    property: str = ""


@dataclass(frozen=True, slots=True)
class RestoredTask:
    """
    A task as its records leave it.

    ``holder`` survives completion, as ``Task.claimed_by`` does: who did the work
    is worth reading afterwards. It is a party of the process that wrote the log,
    and nothing here says the agent is running in the one that reads it. A task
    the operator closed is finished and has none (``closed_by_operator``).
    """

    id: str
    title: str
    declared_by: Party
    # Never empty: the declaration is version one, and each amendment that changed
    # the specification adds one.
    versions: tuple[TaskVersion, ...]
    depends_on: tuple[str, ...] = ()
    touches: tuple[str, ...] = ()
    state: TaskState = TaskState.PENDING
    holder: Party | None = None
    released_by: tuple[Party, ...] = ()
    completed_at: float | None = None
    result: str = ""
    result_basis: str | None = None
    # The version the holder was last handed: the one that stood when it claimed,
    # moved forward by a ``Handed`` record, emptied when the claim is given up and
    # kept by a completion. ``model.Task.claimed_version``.
    claimed_version: int | None = None
    # Everyone who has claimed the task, once each, in the order they first did.
    # Never emptied. ``model.Task.held_by``.
    held_by: tuple[Party, ...] = ()
    writes: ApprovedWrites = ApprovedWrites()
    # ``model.Task.not_run``'s three values, as the completion stated them: None
    # is unsaid, the empty string is "nothing was left unrun".
    not_run: str | None = None
    # When the task was last given back, on the wall clock like every time here.
    released_at: float | None = None
    # The operator closed it: finished with no holder, and ``result`` is the
    # closing words and the operator's reason. ``model.Task.closed_by_operator``.
    closed_by_operator: bool = False
    # The log holds a record about this task that one reducer could not have
    # appended after the ones before it. The task is as the records before that
    # one left it; that record and every later one about it are in
    # ``Board.contested``, unapplied.
    contested: bool = False

    @property
    def detail(self) -> str:
        return self.versions[-1].detail

    @property
    def basis(self) -> str | None:
        return self.versions[-1].basis

    @property
    def version(self) -> int:
        return len(self.versions)

    @property
    def declared_at(self) -> float:
        return self.versions[0].at

    @property
    def completed_version(self) -> int | None:
        """
        Which text the result was written against: the one its author had been
        handed, which an amendment made while it worked does not move. None when
        the task is not finished, and when the operator closed it: no result of
        anybody's work was written.
        """
        return self.claimed_version if self.state is TaskState.COMPLETED else None

    def amended_by(self, party: Party) -> bool:
        """Whether ``party`` wrote any version after the first."""
        return any(version.by == party for version in self.versions[1:])

    # Last in the class: from here down the name is this attribute and no longer
    # the decorator.
    @property
    def property(self) -> str:
        return self.versions[-1].property


@dataclass(frozen=True, slots=True)
class RestoredConcern:
    """A concern and what became of it. ``body`` is the text as last written."""

    id: str
    sender: Party
    recipient: Party
    subject: str
    body: str
    posted_at: float
    task_id: str | None = None
    basis: str | None = None
    state: ConcernState = ConcernState.POSTED
    edited: bool = False
    delivered_at: float | None = None
    incident: bool = False
    # As on ``RestoredTask``: the concern is as the records before the first
    # impossible one left it, and the rest are in ``Board.contested``.
    contested: bool = False


@dataclass(frozen=True, slots=True)
class Board:
    """
    What a log says a board held. Tasks in declaration order, concerns in the order
    they were posted -- the log's order, which is the one ordering here that no
    clock can disturb.

    ``stray`` holds every record that named a task or concern the log never
    introduced. ``contested`` holds every record about one it did introduce that
    one reducer could not have appended after the records before it -- a second
    declaration, a claim on a task somebody holds, a completion by a party that
    does not hold it, an edit of a concern already handed over -- and every later
    record about the same task or concern. Neither kind changes the thing it could
    not be applied to and both are kept, because a board restored short with
    nothing saying so is the failure a log of outcomes exists to rule out. One
    record can be in either and still have been applied elsewhere: a delivery
    marks the concerns it may and is listed for the ones it may not.

    ``stopped`` holds every ``RecordingStopped`` the log carries: each is a point
    where a writer gave up appending, so at least one fact the board once held is
    not in these tasks and concerns, and no record says which. A log whose writer
    stopped and could not write even that -- the disk was full, the directory was
    gone, the process was killed with records still queued -- carries nothing,
    and restores as though it were whole. That case is not detectable from the
    log.

    ``damage`` is where the log stopped being readable, when this was built from
    a ``Log`` by ``restore_log``. ``restore`` is given records and not a log, so
    it leaves this None, which is then not a statement that nothing was lost.

    ``addresses`` is what each node the log names was last called, in the order
    the log first named them. It is the only place a node's address is kept: a
    task and a concern hold the node. A node the board had no name for is not in
    it, and neither is a party the log knows by address alone.

    **Ask** ``short`` **before treating this as the board.** It is the one answer
    to "did the board hold more than these tasks and concerns do", over all five
    places that can say so.

    **A restart reads as a contest unless the claim was given up.** A task that
    was held when a process ended, then claimed and finished by another agent in
    the next, has a second claim no one reducer could have appended: it restores
    held by the first, ``contested``, with the completion unapplied, on every
    restore from then on. ``Reclaimed`` is the record that prevents it. Logged
    before the second claim, it leaves the task pending and the rest is one
    history.
    """

    tasks: tuple[RestoredTask, ...] = ()
    concerns: tuple[RestoredConcern, ...] = ()
    stray: tuple[Record, ...] = ()
    unrecognised: tuple[Unrecognised, ...] = ()
    contested: tuple[Record, ...] = ()
    damage: Damage | None = None
    addresses: tuple[tuple[NodeId, str], ...] = ()
    stopped: tuple[RecordingStopped, ...] = ()

    @property
    def short(self) -> bool:
        """
        Whether the board held anything these tasks and concerns do not show: a
        record past a break, about something never introduced, of a kind this
        build does not define, or withheld as contested -- or a fact that was
        never written, because the log's writer stopped.

        True is not an error to clear. It is what an operator is owed before the
        board is acted on, and ``damage``, ``stray``, ``unrecognised``,
        ``contested`` and ``stopped`` say which.
        """
        return bool(
            self.damage is not None
            or self.stray
            or self.unrecognised
            or self.contested
            or self.stopped
        )


def restore_log(log: Log) -> Board:
    """
    The board a read log describes, carrying where the read stopped.

    ``restore`` of the readable prefix with ``Log.damage`` beside it, so that one
    value answers ``Board.short`` and a caller holding only the board cannot have
    left the damage behind with the log.
    """
    return dataclasses.replace(restore(log.records), damage=log.damage)


def restore(records: Iterable[Record]) -> Board:
    """
    The board a sequence of records describes. Pure and total.

    A fold over facts one reducer settled: whether a claim was allowed was that
    reducer's question and the record is its answer, so a record is applied as
    stated and the last one about a thing is what the thing is.

    **Unless no one reducer could have settled it there.** A log two windows wrote
    holds both reducers' answers, and applied as stated they make a task neither
    window held: a live claim carrying somebody else's result. So each record is
    asked the one thing ``store._apply`` guards -- ``_one_writers`` lists them for
    a task; an edit, a withdrawal and a delivery need a concern not yet handed
    over or stopped; a thing is introduced once. A record that fails marks its
    task or concern ``contested``. From there the log has two histories of it and
    no record says which it belongs to, so the thing stands as it stood and every
    later record about it goes to ``Board.contested`` with the first. This is not
    a second reducer: nothing is refused that one writer could have logged, and
    what two writers logged in an order one could have is applied as one history
    (the module docstring says what that misses).

    It restates the reducer wherever a rule is about history that no single
    record carries: an agent appears once in ``released_by`` however often it
    gives a task back, and once in ``held_by`` however often it claims; an
    amendment empties ``released_by`` because the text that was declined is no
    longer the offer, adds a version only when it changed the text or the
    property, and leaves the standing property in force when it names none; a
    claim is handed the version standing when it is made, a release hands it
    back, and a completion keeps it.

    Applying a log to a store that already holds this session's board is not this
    function's question. The value says what the log holds; a caller that folds it
    into live state twice has two boards' worth of versions.
    """
    tasks: dict[str, RestoredTask] = {}
    concerns: dict[str, RestoredConcern] = {}
    stray: list[Record] = []
    unrecognised: list[Unrecognised] = []
    contested: list[Record] = []
    stopped: list[RecordingStopped] = []
    addresses: dict[NodeId, str] = {}

    for record in records:
        for address, node in _named(record):
            party = _party(address or "", node)
            if address and not isinstance(party, str):
                addresses[party] = address
        match record:
            case Declared():
                again = tasks.get(record.task_id)
                if again is not None:
                    contested.append(record)
                    tasks[record.task_id] = dataclasses.replace(again, contested=True)
                    continue
                declarer = _party(record.declared_by, record.declared_by_id)
                tasks[record.task_id] = RestoredTask(
                    id=record.task_id,
                    title=record.title,
                    declared_by=declarer,
                    versions=(
                        TaskVersion(
                            record.at,
                            record.detail,
                            declarer,
                            record.basis,
                            record.property,
                        ),
                    ),
                    depends_on=record.depends_on,
                    touches=record.touches,
                )
            case (
                Claimed()
                | Released()
                | Reclaimed()
                | Handed()
                | Completed()
                | Closed()
                | Amended()
                | WritesAccrued()
            ):
                task = tasks.get(record.task_id)
                if task is None:
                    stray.append(record)
                    continue
                if task.contested or not _one_writers(task, record):
                    contested.append(record)
                    tasks[record.task_id] = dataclasses.replace(task, contested=True)
                    continue
                tasks[record.task_id] = _task_after(task, record)
            case Posted():
                reposted = concerns.get(record.concern_id)
                if reposted is not None:
                    contested.append(record)
                    concerns[record.concern_id] = dataclasses.replace(reposted, contested=True)
                    continue
                concerns[record.concern_id] = RestoredConcern(
                    id=record.concern_id,
                    sender=_party(record.sender, record.sender_id),
                    recipient=_party(record.recipient, record.recipient_id),
                    subject=record.subject,
                    body=record.body,
                    posted_at=record.at,
                    task_id=record.task_id,
                    basis=record.basis,
                    incident=record.incident,
                )
            case Edited():
                concern = concerns.get(record.concern_id)
                if concern is None:
                    stray.append(record)
                    continue
                if not _waiting(concern):
                    contested.append(record)
                    concerns[record.concern_id] = dataclasses.replace(concern, contested=True)
                    continue
                concerns[record.concern_id] = dataclasses.replace(
                    concern, body=record.body, edited=True
                )
            case Withdrawn():
                concern = concerns.get(record.concern_id)
                if concern is None:
                    stray.append(record)
                    continue
                if not _waiting(concern):
                    contested.append(record)
                    concerns[record.concern_id] = dataclasses.replace(concern, contested=True)
                    continue
                concerns[record.concern_id] = dataclasses.replace(
                    concern, state=ConcernState.WITHDRAWN
                )
            case Delivered():
                # One read can name several concerns, and the mark on the ones it
                # may be applied to must not be lost to one the log does not know
                # or to one two writers disagreed about. So this is the one record
                # that can be applied, stray and contested at once. A concern named
                # twice is handed over once: the repeat is one writer saying one
                # thing again, and is not a second history of the concern.
                if any(cid not in concerns for cid in record.concern_ids):
                    stray.append(record)
                disputed = False
                for cid in dict.fromkeys(record.concern_ids):
                    concern = concerns.get(cid)
                    if concern is None:
                        continue
                    if not _waiting(concern):
                        disputed = True
                        concerns[cid] = dataclasses.replace(concern, contested=True)
                        continue
                    concerns[cid] = dataclasses.replace(
                        concern, state=ConcernState.DELIVERED, delivered_at=record.at
                    )
                if disputed:
                    contested.append(record)
            case RecordingStopped():
                stopped.append(record)
            case Unrecognised():
                unrecognised.append(record)
            case _:
                assert_never(record)

    return Board(
        tasks=tuple(tasks.values()),
        concerns=tuple(concerns.values()),
        stray=tuple(stray),
        unrecognised=tuple(unrecognised),
        contested=tuple(contested),
        addresses=tuple(addresses.items()),
        stopped=tuple(stopped),
    )


_TaskRecord = Claimed | Released | Reclaimed | Handed | Completed | Closed | Amended | WritesAccrued


def _amender(record: Amended) -> Party | None:
    """Who an amendment is by. None is the operator: no address and no node."""
    if record.by is None and not record.by_id:
        return None
    return _party(record.by or "", record.by_id)


def _one_writers(task: RestoredTask, record: _TaskRecord) -> bool:
    """
    Whether one reducer could have appended ``record`` to the records that left
    ``task`` as it is. The guards are ``store._apply``'s, on what a record carries.

    A claim needs a pending task and a claimer that has not amended it. Its other
    condition, that every dependency is finished, is not asked: it is about other
    tasks, and a contest on one of them must not spread. A release and a
    completion need the task held by the party the record names; a reclaim needs
    it held by the party the record says it was taken from. A handed version is
    for the holder, is one the task has had -- versions count from 1, so a claim
    that recorded none bounds it from below as one that recorded the first does
    -- and is not earlier than the one already recorded. A measured write needs
    the task held. The operator's amendment is taken in any state; an agent's
    only from the party that declared the task and has never held it. The
    operator's close is taken in any state but finished.
    """
    match record:
        case Claimed():
            claimer = _party(record.by, record.by_id)
            return task.state is TaskState.PENDING and not task.amended_by(claimer)
        case Released() | Completed():
            return task.state is TaskState.CLAIMED and task.holder == _party(
                record.by, record.by_id
            )
        case Reclaimed():
            return task.state is TaskState.CLAIMED and task.holder == _party(
                record.holder, record.holder_id
            )
        case Handed():
            return (
                task.state is TaskState.CLAIMED
                and task.holder == _party(record.by, record.by_id)
                and (task.claimed_version or 1) <= record.version <= task.version
            )
        case WritesAccrued():
            return task.state is TaskState.CLAIMED
        case Closed():
            return task.state is not TaskState.COMPLETED
        case Amended():
            amender = _amender(record)
            return amender is None or (amender == task.declared_by and amender not in task.held_by)
        case _:
            assert_never(record)


def _waiting(concern: RestoredConcern) -> bool:
    """
    Whether one reducer could still edit, withdraw or deliver ``concern``: it has
    been posted, nothing has become of it, and no earlier record contested it.
    """
    return concern.state is ConcernState.POSTED and not concern.contested


def _once_more(parties: tuple[Party, ...], party: Party) -> tuple[Party, ...]:
    return parties if party in parties else (*parties, party)


def _task_after(task: RestoredTask, record: _TaskRecord) -> RestoredTask:
    """``task`` with one more fact about it applied."""
    match record:
        case Claimed():
            claimer = _party(record.by, record.by_id)
            return dataclasses.replace(
                task,
                state=TaskState.CLAIMED,
                holder=claimer,
                claimed_version=task.version,
                held_by=_once_more(task.held_by, claimer),
            )
        case Released() | Reclaimed():
            return dataclasses.replace(
                task,
                state=TaskState.PENDING,
                holder=None,
                claimed_version=None,
                released_by=_once_more(task.released_by, _party(record.by, record.by_id)),
                released_at=record.at,
            )
        case Handed():
            return dataclasses.replace(task, claimed_version=record.version)
        case Completed():
            return dataclasses.replace(
                task,
                state=TaskState.COMPLETED,
                completed_at=record.at,
                result=record.result,
                result_basis=record.basis,
                not_run=record.not_run,
            )
        case Closed():
            return dataclasses.replace(
                task,
                state=TaskState.COMPLETED,
                completed_at=record.at,
                result=closing_result(record.reason),
                result_basis=None,
                not_run=None,
                holder=None,
                claimed_version=None,
                closed_by_operator=True,
            )
        case Amended():
            held_to = task.property if record.property is None else record.property
            versions = task.versions
            if record.detail != task.detail or held_to != task.property:
                versions += (
                    TaskVersion(record.at, record.detail, _amender(record), record.basis, held_to),
                )
            return dataclasses.replace(task, versions=versions, released_by=())
        case WritesAccrued():
            return dataclasses.replace(
                task,
                writes=task.writes.plus(
                    ApprovedWrites(
                        paths=record.paths,
                        unplaced=record.unplaced,
                        lines_added=record.lines_added,
                        lines_removed=record.lines_removed,
                    )
                ),
            )
        case _:
            assert_never(record)


# -- times ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Clock:
    """
    One reading of both clocks, taken once per restore by the shell.

    **A record's time is wall-clock, and the store's are monotonic.**
    ``declared_at``, ``posted_at`` and their kin are ``time.monotonic()``, which
    counts from boot: a value logged by one boot and compared in the next is larger
    than every new one, so a restored board sorts behind the tasks declared after
    it and a bare claim hands out the newest work first. So no monotonic value is
    ever written, and a restored time is rebuilt as *this far before now*.

    One instance per restore, because two readings taken apart disagree about how
    old every record is by the time between them.
    """

    wall: float
    monotonic: float

    def monotonic_at(self, at: float) -> float:
        """
        The monotonic value this process would have read at wall time ``at``.

        Never later than the reading itself. A wall clock can be stepped, and a
        record stamped in what is now the future would otherwise sort after work
        declared tomorrow; clamped, everything restored is at or before the
        restore, and everything declared afterwards is after it.

        Negative for a record older than this boot, which is correct: the store
        only ever subtracts and compares these.

        A step also means these values need not be in log order among themselves.
        ``Board`` is, and that order is the one to keep.
        """
        return self.monotonic - max(0.0, self.wall - at)

    def wall_at(self, at: float) -> float:
        """
        The wall time at which this process's monotonic clock read ``at``: what a
        store's stamp is written to a record as.

        The inverse of ``monotonic_at`` for any instant up to this reading. A
        stamp from after it comes back as later than the reading, where
        ``monotonic_at`` would clamp; a writer takes its reading no earlier than
        the stamps it converts, so the two round-trip.
        """
        return self.wall - (self.monotonic - at)
