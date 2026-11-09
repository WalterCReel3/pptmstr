"""
The texts a parked edit is shown as, and the unified diff drawn from them.

Works on strings alone: the file an edit targets is read in
``gate.park.diff_texts``, which builds the ``DiffText`` records this renders.

Imports nothing from pptmstr. ``model``'s ``PendingApproval`` carries a
``DiffText``, so an edge into the package would close a cycle through it.
"""

from __future__ import annotations

import difflib
import enum
from dataclasses import dataclass
from typing import assert_never


@dataclass(frozen=True, slots=True)
class DiffText:
    """
    One before/after pair a parked call would produce, as texts rather than a diff.

    Defined here rather than beside ``PendingApproval``: ``model`` imports this
    module, so the record sits on the side of that edge that both can reach.

    ``before`` and ``after`` are either **the whole file** -- so a line number counted
    in them is the file's -- or **fragments**, the call's own strings diffed alone.
    They are fragments whenever ``will_not_apply`` is set, and for every ``MultiEdit``
    part, whose label is ``edit N`` rather than a path.
    """

    # None is "no such file", not "empty file": it is what puts ``/dev/null`` in the
    # unified header. ``diff_texts`` never produces ``after=None`` -- no tool it knows
    # deletes a file -- but the record can say it.
    before: str | None
    after: str | None
    # The path exactly as the call gave it, or a stand-in when it gave none. Never
    # decorated: ``render_diff`` writes it into the header verbatim.
    label: str
    # Why the texts are fragments: the gate's own read says this Edit has no place
    # to land. One of four fixed phrases, because an operator acts differently on
    # each -- see ``_edit_refusal``. A prediction from this read, not the tool's
    # verdict.
    will_not_apply: str | None = None
    # The file exists and could not be read as UTF-8 text (binary, a directory, no
    # permission). On a Write this is the only thing separating "replaces a file
    # whose content could not be shown" from "creates a file": ``before`` is None
    # in both, because the unified diff has always drawn them the same.
    unreadable: bool = False


def render_texts(tool_name: str, texts: tuple[DiffText, ...] | None) -> str | None:
    """
    The unified diff of ``diff_texts``' answer for the same call.

    Separate from ``render_diff`` so a caller that keeps the texts can have both
    from one read of the file. ``tool_name`` is needed because the two shapes join
    differently and the bytes are relied on: a MultiEdit drops its empty parts and
    is None when none are left, where a Write or an Edit that changes nothing is
    the empty string.
    """
    if texts is None:
        return None
    if tool_name == "MultiEdit":
        return "\n".join(p for p in map(_unified, texts) if p) or None
    return "".join(map(_unified, texts))


class _FileState(enum.Enum):
    READ = "read"
    MISSING = "missing"
    UNREADABLE = "unreadable"


def _edit_refusal(state: _FileState, old_string: str) -> str:
    """
    Which of the four reasons an Edit has nowhere to land.

    Ordered so the file's condition is named before the anchor's: an anchor cannot
    be looked for in a file that was never read.
    """
    match state:
        case _FileState.MISSING:
            return "file not found"
        case _FileState.UNREADABLE:
            return "file is not readable as text"
        case _FileState.READ:
            return "old_string is empty" if not old_string else "anchor not found in file"
        case _:
            assert_never(state)


def _unified(text: DiffText) -> str:
    # The a/ b/ prefixes are a git convention for repo-relative paths; on an
    # absolute path they produce "b//tmp/x", which reads as a typo in the header of
    # every diff the operator sees.
    prefix_a, prefix_b = ("", "") if text.label.startswith("/") else ("a/", "b/")
    lines = difflib.unified_diff(
        (text.before or "").splitlines(keepends=True),
        (text.after or "").splitlines(keepends=True),
        fromfile=f"{prefix_a}{text.label}" if text.before is not None else "/dev/null",
        tofile=f"{prefix_b}{text.label}" if text.after is not None else "/dev/null",
        n=3,
    )
    return "".join(lines)


def diff_line_kind(line: str) -> str:
    """
    Classify a diff line for styling: 'add', 'remove', 'meta' or 'context'.

    The caller keeps the leading +/- in the rendered text regardless of colour --
    that gutter is the non-hue channel, and it is what keeps a diff readable in
    high contrast and to a colour-deficient operator (design §6.1).
    """
    if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
        return "meta"
    if line.startswith("+"):
        return "add"
    if line.startswith("-"):
        return "remove"
    return "context"
