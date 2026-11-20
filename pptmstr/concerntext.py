"""
What ``read_inbox`` says of the two concerns no agent wrote: the operator's
notice that a task was closed under its holder, and the notice a withdrawal files
to the agent that held the task.

Cut from ``bus`` unchanged (``planning/2026-11-07-the-bus-is-a-table-of-tools.md``,
B7). Pure: no SDK, no clock, no IO.
"""

from __future__ import annotations

from .board.text import _AS_EDITED, _CLOSED_UNDER_YOU, _WITHDRAWN_UNDER_YOU
from .model import Concern
from .replies import _ANSWER, _named, _one_line, _quoted


def _operators_notice(notice: Concern) -> list[str]:
    """
    The operator's notice that a task was closed under its holder
    (``model.closing_notice``), as the lines an inbox shows it on.

    Not laid out as an agent's concern, because no agent wrote any of it: the
    heading and what the close means are this tool's words, and the body is the
    operator's reason, which stands behind the operator's mark and not behind
    the one that says an agent is being quoted.
    """
    about = f" {_named(notice.task_id)}" if notice.task_id else ""
    lines = [
        f"[from the operator, not from an agent] ABOUT TASK{about}, WHICH YOU HELD. "
        f"{_CLOSED_UNDER_YOU}"
    ]
    if not notice.body.strip():
        return [*lines, "The operator gave no reason."]
    return [
        *lines,
        "The operator's reason:",
        *_quoted(notice.body, len(notice.body), "", mark=_ANSWER)[0],
    ]


def _withdrawal_notice_lines(notice: Concern, who: str) -> list[str]:
    """
    The notice to the holder of a withdrawn task (``model.withdrawal_notice``),
    as the lines an inbox shows it on.

    Not laid out as ``who``'s concern, because ``who`` wrote only the body: the
    subject is pptmstr's sentence, which says who withdrew the task, and is
    shown as this tool's words. The body is the withdrawer's reason, behind the
    mark that quotes an agent, introduced as the operator's rewrite where it is
    one.
    """
    about = f" {_named(notice.task_id)}" if notice.task_id else ""
    edited = f", {_AS_EDITED}" if notice.edited else ""
    return [
        f"[from pptmstr, on behalf of {who}] ABOUT TASK{about}, WHICH YOU HELD. "
        f"{_one_line(notice.subject)}. {_WITHDRAWN_UNDER_YOU}",
        f"The reason {who} gave{edited}:",
        *_quoted(notice.body, len(notice.body), "")[0],
    ]
