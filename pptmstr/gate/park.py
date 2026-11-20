"""
What the driver computes once, when a call parks: its queue row with the records
mark, and the texts of the edit it would make.

Both touch the disk, which is why they run at park time and never per frame:
``summary_of`` measures where the call's paths lead (``gate.reach``) and hands
the mark to the pure ``gate.rows.compose``, and ``diff_texts`` reads the file an
edit would change. Neither writes. No SDK and no UI.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .diff import DiffText, _edit_refusal, _FileState, render_texts
from .reach import records_read, records_written
from .rows import Summary, compose


def summarize(
    tool_name: str,
    tool_input: Mapping[str, Any],
    width: int = 90,
    *,
    session_id: str | None = None,
    cwd: str | None = None,
    reaches: str | Sequence[str] | None = None,
    across: str | None = None,
) -> str:
    """The queue row ``summary_of`` composes, as text alone."""
    return summary_of(
        tool_name,
        tool_input,
        width,
        session_id=session_id,
        cwd=cwd,
        reaches=reaches,
        across=across,
    ).text


def summary_of(
    tool_name: str,
    tool_input: Mapping[str, Any],
    width: int = 90,
    *,
    session_id: str | None = None,
    cwd: str | None = None,
    reaches: str | Sequence[str] | None = None,
    across: str | None = None,
) -> Summary:
    """
    The queue row, as ``gate.rows.compose`` composes it, led by the records mark.

    **A call that reads a session's records says so ahead of everything else.**
    ``records_read`` decides it, from the ``session_id`` and ``cwd`` it is given
    here, and the row leads with whose records they are: the path that reaches
    them may be a relative one or a symlink, and then nothing else on the row
    says why a read is waiting. "Another session's" only when the caller's own
    directory was known and this is not in it. A write into any session's
    records leads with "pptmstr's own records", from ``records_written``.
    """
    mark = ""
    if records_read(tool_name, tool_input, session_id=session_id, cwd=cwd) is not None:
        mark = f"{'another' if session_id else 'a'} session's records · "
    elif records_written(tool_name, tool_input, cwd=cwd) is not None:
        mark = "pptmstr's own records · "
    return compose(tool_name, tool_input, width, mark=mark, reaches=reaches, across=across)


def diff_texts(tool_name: str, tool_input: Mapping[str, Any]) -> tuple[DiffText, ...] | None:
    """
    The texts a call would turn into one another, or None when there is nothing
    diff-shaped to show.

    The one place that decides what a file-mutating call does to a file:
    ``render_diff`` is ``render_texts`` over this, so the unified string and the
    texts cannot describe different edits.

    Reads the current file from disk so the diff is against what is actually there
    -- an Edit's own ``old_string`` is what the model *believes* is there, and the
    difference between those two is exactly what review is for. That read is the
    only IO, and it is why this runs once at park time and never per frame.

    - **Write**: the file against the new content. ``before`` is None for a file that
      is not there and for one that cannot be read; ``unreadable`` tells them apart.
    - **Edit**: the whole file before and after, ``replace_all`` honoured, when the
      anchor is in it. Otherwise the two strings alone, with ``will_not_apply``.
    - **MultiEdit**: one part per edit, always fragments. The file is not read, so
      nothing is claimed about whether they apply.
    """
    if tool_name == "Write":
        path = str(tool_input.get("file_path") or "")
        new = str(tool_input.get("content") or "")
        current, state = _read(path)
        return (DiffText(current, new, path, unreadable=state is _FileState.UNREADABLE),)

    if tool_name == "Edit":
        path = str(tool_input.get("file_path") or "")
        old_string = str(tool_input.get("old_string") or "")
        new_string = str(tool_input.get("new_string") or "")
        current, state = _read(path)
        if current is not None and old_string and old_string in current:
            replaced = current.replace(
                old_string, new_string, -1 if tool_input.get("replace_all") else 1
            )
            return (DiffText(current, replaced, path),)
        # Show the intended replacement rather than nothing -- "this edit will not
        # apply" is itself something the operator wants to see before approving.
        return (
            DiffText(
                old_string,
                new_string,
                path or "(anchor not found in file)",
                will_not_apply=_edit_refusal(state, old_string),
                unreadable=state is _FileState.UNREADABLE,
            ),
        )

    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return None
        # Numbered by position in the call, so a malformed entry leaves a gap rather
        # than renumbering the edits after it.
        parts = tuple(
            DiffText(
                str(edit.get("old_string") or ""),
                str(edit.get("new_string") or ""),
                f"edit {i}",
            )
            for i, edit in enumerate(edits, 1)
            if isinstance(edit, dict)
        )
        return parts or None

    return None


def render_diff(tool_name: str, tool_input: Mapping[str, Any]) -> str | None:
    """
    A unified diff of what the call would change, or None when there is nothing
    diff-shaped to show.

    Returning None is meaningful rather than a failure: a Bash command has no diff,
    and inventing one would be worse than the summary the caller already has. The
    detail pane branches on it.

    Reads the file; see ``diff_texts``.
    """
    return render_texts(tool_name, diff_texts(tool_name, tool_input))


def _read(path: str) -> tuple[str | None, _FileState]:
    if not path:
        return None, _FileState.MISSING
    try:
        return Path(path).read_text(encoding="utf-8"), _FileState.READ
    except (FileNotFoundError, NotADirectoryError):
        # Missing file is the common case for Write, and it is not an error --
        # the diff is then simply "everything is new".
        return None, _FileState.MISSING
    except (OSError, UnicodeDecodeError, ValueError):
        return None, _FileState.UNREADABLE
