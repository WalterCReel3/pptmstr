"""
Every id the bus mints, the form each is minted in, and the shapes ids are read
back by.

No import from pptmstr and no clock: a mint that needs the time is handed it.
Not pure, because a mint reads entropy (``uuid4``), and this is the module that
does: ``tests/test_layers.py`` holds ``uuid`` and ``secrets`` to it.

**A request id carries no meaning.** It is a key into ``Bridge._requests`` and
nothing reads any part of it, so every tool's is the one form. Uniqueness rests on
its random bits alone; a prefix would add nothing to that and would be a claim
about the tool that no code checks.

**A domain id's form is a file format.** A concern's, a decision's, a task's and a
command's id are written to board and command logs and shown to agents, and two
are parsed back -- a concern's ``re`` by ``is_concern_id``, a command's directory
name by ``is_command_id`` -- so each prefix and width here is pinned exactly by
``tests/test_ids.py``.

The design is ``planning/2026-11-07-channels-and-ids-have-one-owner-each.md``.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Form:
    """
    One minted kind: a fixed prefix and that many lower-case hex digits after it.
    """

    prefix: str
    hex_chars: int

    def mint(self) -> str:
        return f"{self.prefix}{uuid.uuid4().hex[: self.hex_chars]}"

    def fits(self, text: str) -> bool:
        """
        Whether ``text`` is exactly this form: ASCII lower-case hex only, so no
        separator, no dot, and no trailing newline.
        """
        return (
            len(text) == len(self.prefix) + self.hex_chars
            and text.startswith(self.prefix)
            and all(c in _HEX_DIGITS for c in text[len(self.prefix) :])
        )


_HEX_DIGITS = frozenset("0123456789abcdef")

REQUEST = Form("req-", 12)
CONCERN = Form("c-", 12)
DECISION = Form("dq-", 12)
# Only when the declaring agent names none; an agent's own id is kept as given.
TASK = Form("t-", 8)
# Preceded by ``ID_TIME_FORMAT``'s stamp and a hyphen: ``cmd-<utc>-<hex>``.
COMMAND = Form("cmd-", 12)

FORMS: tuple[Form, ...] = (REQUEST, CONCERN, DECISION, TASK, COMMAND)

ID_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
ID_HEX_CHARS = COMMAND.hex_chars

# ``[0-9]`` and not ``\d``, which in a ``str`` pattern matches every Unicode
# decimal digit.
_COMMAND_ID = re.compile(rf"cmd-[0-9]{{8}}T[0-9]{{6}}Z-[0-9a-f]{{{ID_HEX_CHARS}}}")


# -- the mints --------------------------------------------------------------------


def request() -> str:
    return REQUEST.mint()


def concern() -> str:
    return CONCERN.mint()


def decision() -> str:
    return DECISION.mint()


def task() -> str:
    return TASK.mint()


def command(at: float) -> str:
    """
    A command's id, stamped with ``at`` in UTC to the second. ``at`` is the wall
    clock the caller read: the command's files are stamped with the same.
    """
    stamp = time.strftime(ID_TIME_FORMAT, time.gmtime(at))
    return f"{COMMAND.prefix}{stamp}-{uuid.uuid4().hex[:ID_HEX_CHARS]}"


# -- the shapes ids are read back by ----------------------------------------------


def is_concern_id(text: str) -> bool:
    """Whether ``text`` has the shape of an id the bus gives a concern."""
    return CONCERN.fits(text)


def is_command_id(s: object) -> bool:
    """
    Whether ``s`` has the form of a command id.

    An id names a directory, so this is what stands between a string from a file
    or an agent and a path: the form admits no separator, no dot, and no
    character outside ASCII digits, lower-case hex and the fixed letters. The
    whole string must match -- a trailing newline does not.
    """
    return isinstance(s, str) and _COMMAND_ID.fullmatch(s) is not None
