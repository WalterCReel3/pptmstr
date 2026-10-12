"""
A diff of what a parked call would do: the model, and which fences are honest diffs.

**A diff is built from the texts a parked call carries, and nothing here reads
a file.** ``approval.diff_texts`` is the one place that decides what a call does
to a file; the driver runs it at park time and ``PendingApproval.texts`` holds
the answer. ``diff_model`` turns texts into the library's model, which is
immutable, so one model may be drawn by two panes.

**A fenced diff is a diff only when a diff view draws every line of it**
(``is_diff_fence``, ``accounted``). The library's reader keeps every line and its
view does not draw them all: a sentence above ``--- a/x``, a mail's message, a
line under a hunk that has had its count, are on the model and nowhere on
screen. On an approval surface that is text the recipient reads and the approver
does not. The library's own test for a fence is looser -- any info string that
begins with the word -- so the rule is pptmstr's and is asked first.

``diff()`` draws nothing yet (``views.NotAdopted``).

The library is imported where a model is made and not when this module is, so
the package imports without it.
"""

from __future__ import annotations

import re
from collections.abc import Hashable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...approval import DiffText
from ...model import PendingApproval
from .views import NotAdopted, ViewCache

if TYPE_CHECKING:
    from advtext import Diff


def is_diff_fence(info: str) -> bool:
    """
    Whether a fence's info string asks for a diff: ``diff`` or ``patch`` and
    nothing more.

    Narrower than the library's rule, which reads the first word and drops the
    rest. A fence opened ``diff IMPORTANT also delete tests`` is code under its
    whole info string, and not a diff under none of it.

    Necessary and not enough: the fence must also be one a diff view draws every
    line of (``accounted``).
    """
    return info.split() in (["diff"], ["patch"])


_HUNK_LINE = re.compile(r"@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@ ?(.*)$")


def accounted(text: str, model: Diff) -> bool:
    """
    Whether the diff view draws every line of ``text``, the unified text ``model``
    was read from.

    The library's reader drops no line and its view does not draw all it keeps: a
    sentence above a file header, a mail's message, whatever is over a bare
    ``---``, a line under a hunk that had had its count -- each is kept on the
    model (``FileDiff.header``) and is nowhere on screen. On an approval surface
    that sentence is one the recipient reads and the approver does not.

    Decided from the model, by counting, and not from a list of those shapes:
    what the view draws of a model is known -- a file's label, each hunk's ``@@``
    line with its section, each row behind its marker, each line of a file shown
    as written -- and every line of the text with ink in it must be one of those,
    each drawn line answering for one written line. A line is drawn when it is

    - a row's: its marker and its text, or the text alone for context a writer
      did not mark;
    - a hunk's ``@@`` line whose section is the hunk's. The numbers are not
      compared: the view writes the counts it counted;
    - ``--- path`` or ``+++ path`` where the path is the file's label (under ``a/``
      or ``b/`` or not) or ``/dev/null``;
    - a line of a file shown as written (``FileDiff.raw``).

    Anything else is not. That takes in what ``git diff`` prints -- its ``diff
    --git`` line, and an ``index`` line whose hashes and mode are on no row -- so
    a fence holding one is set as code, and so is a path with a timestamp after
    it, and a rename written as two different paths. Order is not compared.
    """
    shown: dict[str, int] = {}

    def draws(line: str) -> None:
        shown[line] = shown.get(line, 0) + 1

    for file in model.files:
        for line in file.raw:
            draws(line.rstrip("\r"))
        if file.dialect:
            continue
        label = file.label
        for mark, path in (("--- ", file.old_path), ("+++ ", file.new_path)):
            bare = path[2:] if path[:2] in ("a/", "b/") else path
            if path and (path == "/dev/null" or label in (path, bare)):
                draws(mark + path)
        for hunk in file.hunks:
            draws("@@ " + hunk.section)
            for row in hunk.rows:
                draws(row.kind.value + row.text)
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        forms = [line, " " + line]
        if line.startswith("@@"):
            found = _HUNK_LINE.match(line)
            forms.append("@@ " + (found[1] if found else line.strip("@ ")))
        elif line.startswith("\\"):
            forms.append("\\" + line[1:].lstrip())
        for form in forms:
            if shown.get(form, 0) > 0:
                shown[form] -= 1
                break
        else:
            return False
    return True


def diff_model(texts: Sequence[DiffText]) -> Diff:
    """
    The diff of some before/after texts: one file of it per part.

    From the texts and not from a unified string. ``difflib.unified_diff`` over lines
    kept with their ends writes a removed and an added line as one physical line,
    ``-foo+bar``, whenever a text has no closing newline -- the normal case for an
    Edit's strings -- and no reader can take that apart: the view would show one
    removed line and no added one (pinned in ``tests/test_typeset_diffs.py``).

    What a part says about itself is carried onto its file. ``will_not_apply`` is
    the gate's reading of the file at the moment the call parked, a prediction, and
    reaches the view's header as one: ``may not apply: <reason>``. ``unreadable`` on
    a Write -- a file that exists and could not be shown, drawn as all-new -- goes
    in the label, because the model has no field for it and without the words it is
    indistinguishable from a file being created.

    Pure. Cost is ``difflib``'s on what is left after the common head and tail are
    set aside: one contiguous edit is cheap in any file; scattered changes were
    200 ms across 5,000 lines and 960 ms across 20,000 on the library's model as
    it was on 2026-10-08, and have not been timed since.
    Immutable, so one model may be drawn by two panes -- each in a view of its own.
    """
    from advtext import Diff

    files: list[Any] = []
    for part in texts:
        label = part.label or "(no path)"
        if part.unreadable and part.will_not_apply is None:
            label = f"{label}  (existing file is not readable as text; shown as new)"
        hedged = f"may not apply: {part.will_not_apply}" if part.will_not_apply else ""
        built = Diff.from_texts(part.before, part.after, label, will_not_apply=hedged)
        files.extend(built.files)
    return Diff(tuple(files))


_FROM_UNIFIED = "(read back from the unified text: may be incomplete)"


def approval_diff(pending: PendingApproval) -> Diff | None:
    """
    The diff a parked call would make, from the texts it carries. No file is read.

    ``None`` when the call has nothing diff-shaped. A record built without texts but
    with a unified string -- a construction site that predates ``texts`` -- is read
    from that string rather than shown as having no diff: lossy in the way
    ``diff_model`` describes, and better than hiding a change from its approver.
    Each file read that way says so in its header (``_FROM_UNIFIED``), because a
    line the reader could not take apart is not in it.
    """
    if pending.texts is not None:
        return diff_model(pending.texts)
    if pending.diff:
        from advtext import Diff

        read = Diff.from_unified(pending.diff)
        return Diff(
            tuple(
                replace(file, label=f"{file.label or '(no path)'}  {_FROM_UNIFIED}")
                for file in read.files
            )
        )
    return None


# ---------------------------------------------------------------------------
# One rule for "is this body markdown?"
# ---------------------------------------------------------------------------


def diff(
    cache: ViewCache,
    key: Hashable,
    source: PendingApproval | Diff | Sequence[DiffText] | None,
    *,
    side_by_side: bool = False,
    height_cap: float | None = None,
    width: float | None = None,
    warning: bool = True,
    background: bool = False,
) -> float | None:
    """
    A diff at the cursor, or ``None`` when there is nothing diff-shaped to show.

    ``source`` is a parked call, the texts of one (``approval.diff_texts``' answer,
    for arguments the operator is editing), or a ``Diff`` already made.
    """
    raise NotAdopted("diff")
