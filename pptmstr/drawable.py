"""
Text a session produced, in a form every text call can be handed.

A string that came out of a session is not always text. ``json.loads('"\\udc80"')``
is a lone surrogate, so a tool's arguments, a tool's result and a model's output
can each carry one, and a NUL arrives the same way. Two things downstream cannot
take them:

- **Every ImGui text call raises ``TypeError`` on a lone surrogate** -- measured
  over ``text``, ``text_unformatted``, ``text_wrapped``, ``text_colored``,
  ``text_disabled``, ``calc_text_size``, ``button``, ``selectable``,
  ``set_tooltip``, ``input_text``, ``input_text_multiline``, a draw list's
  ``add_text``, ``set_clipboard_text``, ``push_id`` and ``begin_child`` -- because
  the binding encodes its argument as UTF-8 and a surrogate has no encoding. One
  such string in a row is the whole pane not drawn. A strict ``str.encode`` and a
  ``print`` to a UTF-8 stream raise on it too.
- **ImGui reads a string to its first NUL and raises nothing.**
  ``calc_text_size("a\\x00bcdef")`` is the width of ``"a"``: the rest of a command
  is neither measured nor drawn, and the pane reports it whole.

Nothing else was found to do either: characters outside the BMP, U+FFFE and
U+FFFF, C0 and C1 controls, bidirectional controls, ``%`` sequences and ``##`` all
pass through those calls and are drawn or not as the font has them.

**What it is written as.** Each such code unit becomes the six characters
``\\uXXXX`` in lower-case hex: ``\\udc80``, ``\\u0000``. That is the form JSON
gives them, which is the form the arguments editor already shows (``json.dumps``)
and the one the code unit arrived in, so the row and the editor say one thing.

**It cannot be mistaken for the same six characters written out.** A command
holding the surrogate and a command holding a backslash, a ``u`` and four hex
digits are different commands, and one is approved from what is drawn. So a
backslash that would begin such a sequence -- one this module writes, in either
case of hex -- is itself written ``\\u005c``, and so is one that would begin
``\\u005c``. Read back: ``\\u`` followed by ``0000``, ``005c`` or a surrogate's four
lower-case digits is always the code unit, and no other backslash is touched. The
mapping is one to one (``original`` inverts it), which is what makes it safe to
apply to an identifier as well as to prose: two different strings stay different.

**Where it is applied** is where session text enters what pptmstr holds: the
reducer (``store._apply``), ``Transcript.append``, the log ring, and the listing of
sessions on disk (``sessions.merge_overlay``). Not at the calls that draw, which are
several hundred, and not on the way out: what a tool is run with is never read
from here (``PendingApproval.raw_args``).

No import from ``pptmstr``: the transcript, the log and the model all sit below
anything that could otherwise hold this.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from typing import Any, TypeVar

T = TypeVar("T")

# A code unit no text call takes, or a backslash that would spell the escape of one.
_ESCAPED = re.compile(r"[\x00\ud800-\udfff]|\\(?=u(?:0000|005[cC]|[dD][89a-fA-F][0-9a-fA-F]{2}))")
_WRITTEN = re.compile(r"\\u(0000|005c|d[89a-f][0-9a-f]{2})")

# Per record type, the fields ``shown`` leaves exactly as they are.
Verbatim = Mapping[type, frozenset[str]]


def _write(match: re.Match[str]) -> str:
    return f"\\u{ord(match[0]):04x}"


def text(value: str) -> str:
    """
    ``value`` with every code unit a text call cannot take written as ``\\uXXXX``;
    the same object when it holds none, which is nearly always.

    Asked once of each string as it enters, so the common answer is the cheap one:
    two substring searches and an encode, 0.2 ms for 100 kB that is not ASCII. The
    encode is how a lone surrogate is found without a pass in Python.
    """
    if "\x00" not in value and "\\u" not in value:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            pass
        else:
            return value
    return _ESCAPED.sub(_write, value)


def original(written: str) -> str:
    """
    The string ``text`` was given, from what it answered.

    Nothing in the product calls this. It is the proof that ``text`` loses nothing
    and merges nothing, kept beside it so that the two are changed together.
    """
    return _WRITTEN.sub(lambda match: chr(int(match[1], 16)), written)


def shown(value: T, verbatim: Verbatim | None = None) -> T:
    """
    ``value`` with every string in it passed through ``text``: a string, or
    strings held by tuples, lists, sets, mappings (keys and values) and
    dataclass records, to any depth. **The same object wherever nothing
    changed**, so a record that was already drawable is not copied and whatever
    is keyed on its identity stays keyed.

    ``verbatim`` names, per record type, fields that are acted on and not only
    read, and are left exactly as they are. Anything that is none of the above --
    a number, an enum member, a transcript -- is returned as it is.
    """
    if isinstance(value, str):
        return text(value)  # type: ignore[return-value]
    if isinstance(value, tuple | list | frozenset | set):
        items = [shown(item, verbatim) for item in value]
        if all(new is old for new, old in zip(items, value, strict=True)):
            return value
        if isinstance(value, tuple) and hasattr(value, "_fields"):
            return type(value)(*items)  # type: ignore[return-value]
        return type(value)(items)  # type: ignore[return-value]
    if isinstance(value, Mapping):
        pairs = [(shown(key, verbatim), shown(item, verbatim)) for key, item in value.items()]
        if all(
            k is key and v is item for (k, v), (key, item) in zip(pairs, value.items(), strict=True)
        ):
            return value
        return dict(pairs)  # type: ignore[return-value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        kept = (verbatim or {}).get(type(value), frozenset())
        changed: dict[str, Any] = {}
        for field in dataclasses.fields(value):
            if not field.init or field.name in kept:
                continue
            held = getattr(value, field.name)
            new = shown(held, verbatim)
            if new is not held:
                changed[field.name] = new
        return dataclasses.replace(value, **changed) if changed else value
    return value
