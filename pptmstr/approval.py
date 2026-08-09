"""
Classification and presentation for the approval gate.

Pure functions: no SDK, no UI, no IO except reading the file an edit would change.
Everything here decides *whether* a tool call needs a human and *what the human
should be shown* -- the parking and awaiting live in the driver and the Bridge.

The classification rule is fail-closed. An unrecognised tool requires approval,
because the tool this orchestrator has never heard of is precisely the one that
should not run unreviewed, and an allowlist that defaults open stops being an
allowlist the first time the SDK adds a tool.
"""

from __future__ import annotations

import difflib
import enum
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class Disposition(enum.Enum):
    AUTO_APPROVE = "auto_approve"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


# Reads, searches and listings. Cheap, reversible, and they do not leave the box.
_AUTO = frozenset(
    {
        "Read",
        "Glob",
        "Grep",
        "NotebookRead",
        "TodoWrite",
        "ListMcpResources",
        "ReadMcpResource",
    }
)

# Mutating, or reaching the network. Named explicitly so the list reads as a
# decision rather than as whatever happened to be left over.
_REVIEW = frozenset(
    {
        "Write",
        "Edit",
        "MultiEdit",
        "NotebookEdit",
        "Bash",
        "BashOutput",
        "KillShell",
        "WebFetch",
        "WebSearch",
        "Task",
        "Agent",
    }
)


def classify(tool_name: str, tool_input: Mapping[str, Any]) -> Disposition:
    """
    Whether a tool call may run unattended.

    ``Task``/``Agent`` require approval deliberately: spawning a sub-agent is a
    tool call like any other, and an orchestrator that gates writes but not the
    spawning of things that write has a hole in it.
    """
    if tool_name in _AUTO:
        return Disposition.AUTO_APPROVE
    if tool_name in _REVIEW:
        return Disposition.REQUIRE_APPROVAL
    # Unknown, including every MCP tool this build has never seen.
    return Disposition.REQUIRE_APPROVAL


def summarize(tool_name: str, tool_input: Mapping[str, Any], width: int = 90) -> str:
    """
    One line naming what the call would do. This is the queue row.

    Reads as an action rather than as a serialised argument dict, because the queue
    is scanned rather than read -- the operator is deciding which item to look at,
    not deciding the item.
    """

    def clip(text: str, limit: int = width) -> str:
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 3] + "..."

    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "?")
        return clip(f"{tool_name} {path}")
    if tool_name == "Bash":
        return clip(str(tool_input.get("command") or ""))
    if tool_name in ("WebFetch", "WebSearch"):
        return clip(f"{tool_name} {tool_input.get('url') or tool_input.get('query') or ''}")
    if tool_name in ("Task", "Agent"):
        subtype = tool_input.get("subagent_type") or "agent"
        return clip(f"spawn {subtype}: {tool_input.get('description') or ''}")
    if not tool_input:
        return tool_name
    key = next(iter(tool_input))
    return clip(f"{tool_name} {key}={tool_input[key]!r}")


def render_diff(tool_name: str, tool_input: Mapping[str, Any]) -> str | None:
    """
    A unified diff of what the call would change, or None when there is nothing
    diff-shaped to show.

    Returning None is meaningful rather than a failure: a Bash command has no diff,
    and inventing one would be worse than the summary the caller already has. The
    detail pane branches on it.

    Reads the current file from disk so the diff is against what is actually there
    -- an Edit's own ``old_string`` is what the model *believes* is there, and the
    difference between those two is exactly what review is for.
    """
    if tool_name == "Write":
        path = str(tool_input.get("file_path") or "")
        new = str(tool_input.get("content") or "")
        return _unified(_read(path), new, path)

    if tool_name == "Edit":
        path = str(tool_input.get("file_path") or "")
        old_string = str(tool_input.get("old_string") or "")
        new_string = str(tool_input.get("new_string") or "")
        current = _read(path)
        if current is not None and old_string and old_string in current:
            replaced = current.replace(
                old_string, new_string, -1 if tool_input.get("replace_all") else 1
            )
            return _unified(current, replaced, path)
        # The file is unreadable, or the anchor is not in it. Show the intended
        # replacement rather than nothing -- "this edit will not apply" is itself
        # something the operator wants to see before approving.
        return _unified(old_string, new_string, path or "(anchor not found in file)")

    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return None
        parts = []
        for i, edit in enumerate(edits, 1):
            if not isinstance(edit, dict):
                continue
            parts.append(
                _unified(
                    str(edit.get("old_string") or ""),
                    str(edit.get("new_string") or ""),
                    f"edit {i}",
                )
            )
        return "\n".join(p for p in parts if p) or None

    return None


def _read(path: str) -> str | None:
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Missing file is the common case for Write, and it is not an error --
        # the diff is then simply "everything is new".
        return None


def _unified(before: str | None, after: str, label: str) -> str:
    # The a/ b/ prefixes are a git convention for repo-relative paths; on an
    # absolute path they produce "b//tmp/x", which reads as a typo in the header of
    # every diff the operator sees.
    prefix_a, prefix_b = ("", "") if label.startswith("/") else ("a/", "b/")
    lines = difflib.unified_diff(
        (before or "").splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{prefix_a}{label}" if before is not None else "/dev/null",
        tofile=f"{prefix_b}{label}",
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
