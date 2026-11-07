"""
The two functions B1 changes, as the design proposes them, outside ``pptmstr/``.

A stand-in for ``pptmstr/journal/codec.py`` and ``pptmstr/journal/files.py`` so
that ``readback.py`` can read logs HEAD wrote through the proposed path before any
code moves. Only what changes is here. Everything that moves unchanged --
``encode``, ``append``, ``next_ordinal``, ``_ordinals``, ``_first_gap``,
``session_dir``, ``_as``, and the classes ``Malformed``, ``Unrecognised``,
``RecordingStopped``, ``Damage``, ``Log`` and ``Clock`` -- is taken from HEAD's
``boardlog`` as it is, because a move does not change a body, and ``readback.py``
checks by AST that the two bodies here differ from HEAD's only by the stated
substitutions.

The one new value is ``Kinds``: the table ``decode`` was closed over as the
module globals ``_KINDS`` and ``_HINTS``, now a parameter, so a board log and a
channel log each bring their own. ``RecordingStopped`` is in every table, because
it is the writer's statement about the log and means the same thing in either.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_type_hints

from pptmstr.boardlog import (
    _FIELD_TYPES,
    MAX_RECORD_BYTES,
    Damage,
    Log,
    Malformed,
    RecordingStopped,
    Unrecognised,
    _as,
    _name,
    _ordinals,
)
from pptmstr.ondisk import read_regular

name = _name

# A journal's own union of what it reads; the board's is ``boardlog.Record``. The
# design makes ``Log`` generic in it, which changes no runtime value.
Record = Any


@dataclass(frozen=True)
class Kinds:
    """The record kinds one journal defines, by ``KIND``, and each one's field types."""

    by_name: Mapping[str, type[Any]]
    hints: Mapping[type[Any], dict[str, Any]]


def kinds(*classes: type[Any]) -> Kinds:
    """
    A journal's table: ``classes`` and ``RecordingStopped``. The checks are the
    ones ``boardlog`` runs at import over its own table.
    """
    every = (*classes, RecordingStopped)
    by_name = {cls.KIND: cls for cls in every}
    assert len(by_name) == len(every), "two record kinds share a name"
    hints = {cls: get_type_hints(cls) for cls in every}
    for cls, each in hints.items():
        for field in dataclasses.fields(cls):
            assert each[field.name] in _FIELD_TYPES, (cls.__name__, field.name)
    return Kinds(by_name, hints)


def decode(text: str, kinds: Kinds) -> Any:
    if not text.endswith("\n"):
        return Malformed("no terminator: the write did not finish")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return Malformed(f"not JSON: {exc}")
    except RecursionError:
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
    record: Record = cls(**values)
    return record


def read(directory: Path, kinds: Kinds) -> Log:
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
            text = read_regular(directory / name(ordinal), MAX_RECORD_BYTES).decode("utf-8")
        except (OSError, ValueError) as exc:
            return Log(tuple(records), Damage(ordinal, f"{type(exc).__name__}: {exc}", unread - 1))
        record = decode(text, kinds)
        if isinstance(record, Malformed):
            return Log(tuple(records), Damage(ordinal, record.reason, unread - 1))
        records.append(record)
    return Log(tuple(records))
