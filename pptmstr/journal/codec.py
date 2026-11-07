"""
One journal record as text, and back. Pure: no IO, no clock, and no journal's own
kinds.

**The envelope is every journal's.** A record is a JSON object with a ``kind`` and
a time ``at``, escaped to ASCII and ended by a newline. Its other fields are each
one of a few shapes (``_FIELD_TYPES``), which is what lets one codec serve every
kind of every journal.

**The kinds are a parameter, not a registry.** ``decode`` looks a record's kind up
in the ``Kinds`` it is handed. A table each journal added to at import would be
mutable state whose contents depend on import order; two journals with two closed
vocabularies are two values. ``kinds`` builds one and always adds
``RecordingStopped``, the writer's statement about the log itself, which means the
same thing in any journal.
"""

from __future__ import annotations

import dataclasses
import json
import math
import types
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Generic, Protocol, TypeVar, get_type_hints

# Text at the boundary: an address, a basis, an id. Every field of every record is
# one of these shapes, which is what lets one codec serve all of them. ``bool |
# None`` is a three-valued statement -- yes, no, not said -- which is a different
# thing from a flag whose False claims nothing (``boardlog.Raised.reversible``).
_FIELD_TYPES: tuple[Any, ...] = (str, str | None, float, int, bool, bool | None, tuple[str, ...])

# The most of a failure's reason a ``RecordingStopped`` carries. A reason is an
# exception's text and has no bound of its own, and the marker has to land in the
# case where the record it stands for was too long to.
MARKER_REASON_CHARS = 400


@dataclass(frozen=True, slots=True)
class RecordingStopped:
    """
    The writer of this log stopped appending to it here: a record after the ones
    before this did not land, and nothing was appended behind the hole.

    Not a fact about a task, a concern or a decision. It is what makes the records
    before it read as a prefix rather than as the board: without it a log that
    ends because a completion could not be written is indistinguishable from one
    where the task was never finished. ``boardlog.restore`` lists it in
    ``Board.stopped``, which makes the board ``short``, and applies whatever
    follows it as it would anywhere -- a later process that restored the prefix
    and carried on is one history from that prefix, and the board it leaves is
    still short one fact or more, for good.

    A build older than this kind reads it as ``Unrecognised``, which is also
    ``short``.

    ``reason`` is the writer's words for why the record did not land, cut by the
    writer to a length that leaves this record far under
    ``files.MAX_RECORD_BYTES``: the record that says a record was too long must
    not be.
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


class Kinded(Protocol):
    """
    A record of a kind some table defines: a frozen dataclass with a ``KIND``
    whose fields are each one of ``_FIELD_TYPES``.
    """

    KIND: ClassVar[str]
    __dataclass_fields__: ClassVar[dict[str, dataclasses.Field[Any]]]


R = TypeVar("R", covariant=True)


@dataclass(frozen=True, slots=True)
class Kinds(Generic[R]):
    """
    The record kinds one journal defines, by ``KIND``, and each one's field types.

    ``R`` is that journal's union of what it reads, ``RecordingStopped`` and
    ``Unrecognised`` among them. Build one with ``kinds``, which checks it.
    """

    by_name: Mapping[str, type[Any]]
    hints: Mapping[type[Any], Mapping[str, Any]]


def kinds(*classes: type[Any]) -> Kinds[Any]:
    """
    A journal's table: ``classes`` and ``RecordingStopped``.

    Raises ``AssertionError`` where two kinds share a name, or a field has a
    shape the codec does not know: such a field would encode and then fail every
    read, so it stops the import of the journal that defines it instead.
    """
    every: tuple[type[Any], ...] = (*classes, RecordingStopped)
    by_name = {cls.KIND: cls for cls in every}
    assert len(by_name) == len(every), "two record kinds share a name"
    hints = {cls: get_type_hints(cls) for cls in every}
    for cls, each in hints.items():
        for field in dataclasses.fields(cls):
            assert each[field.name] in _FIELD_TYPES, (cls.__name__, field.name)
    return Kinds(by_name, hints)


@dataclass(frozen=True, slots=True)
class Malformed:
    """Why a file's text is not a record."""

    reason: str


def encode(record: Kinded | Unrecognised) -> str:
    """
    One record as the text of its file: a JSON object and a newline.

    The newline is the terminator, not decoration. Without it the text one byte
    short of a whole record would itself parse, and with it no proper prefix of an
    encoded record decodes -- which is what lets a reader tell a torn file from a
    whole one without a length or a checksum.

    Escaped to ASCII. Agent prose can carry a lone surrogate, which UTF-8 refuses
    to encode and JSON's escapes carry unchanged.

    Raises ``ValueError`` for a time that is not finite; ``files.append`` turns
    that into a value.
    """
    if isinstance(record, Unrecognised):
        return record.text + "\n"
    payload: dict[str, Any] = {"kind": record.KIND}
    for field in dataclasses.fields(record):
        value = getattr(record, field.name)
        payload[field.name] = list(value) if isinstance(value, tuple) else value
    return json.dumps(payload, allow_nan=False) + "\n"


def decode(text: str, kinds: Kinds[R]) -> R | Unrecognised | Malformed:
    """
    The record a file holds under ``kinds``, or why it holds none.

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

    cls = kinds.by_name.get(kind)
    if cls is None:
        return Unrecognised(at=at, kind=kind, text=text[:-1])

    values: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        if field.name not in payload:
            if field.default is dataclasses.MISSING:
                return Malformed(f"{kind}: no {field.name}")
            continue
        value = _as(payload[field.name], kinds.hints[cls][field.name])
        if isinstance(value, Malformed):
            return Malformed(f"{kind}.{field.name}: {value.reason}")
        values[field.name] = value
    record: R = cls(**values)
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
    if hint == bool | None:
        if value is None or isinstance(value, bool):
            return value
        return Malformed("not true, false or null")
    if isinstance(hint, types.UnionType):
        return value if value is None or isinstance(value, str) else Malformed("not text or null")
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return Malformed("not a list of text")
    return tuple(value)


# -- what a read yields -------------------------------------------------------------


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
class Log(Generic[R]):
    """What a directory yielded: the readable prefix, and where it ended if early."""

    records: tuple[R, ...] = ()
    damage: Damage | None = None
