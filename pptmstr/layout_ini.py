"""
A pane added to the app, for an operator whose layout is already saved.

**hello_imgui throws a saved arrangement away when the app gains a pane.** It
keeps one ini part per layout (``;;;<<<ImGui_<n>_<LAYOUT>>>>;;;``), and on loading
a layout it looks in that part for a ``[Window][<label>]`` entry for every
dockable window the code declares. One label without an entry and the layout is
rebuilt from the coded splits: every pane the operator dragged somewhere goes back
to where ``app._triage_layout`` and ``app._focus_layout`` put it. Measured with
``scripts/verify_new_pane_keeps_layout.py`` (imgui_bundle 1.92.801, null backend):
adding DIGEST to the declared windows moved BOARD out of the split it had been
dragged into, in both layouts. It is the entry's presence that is read -- one with
no ``DockId`` keeps the layout and leaves the new pane floating -- and dock
settings made in memory before the first frame do not stop the rebuild, so the
only place to answer is the file, before the runner reads it.

So this adds the missing entry, in the node of the pane the new one is organised
with, to each saved layout that has that pane and lacks the new one. The operator's
arrangement is kept and the new pane arrives as the last tab of that node.

**Insert-only.** Every byte already in the file is still in it afterwards, in
order; ``with_window_beside`` returns its argument unchanged when there is nothing
to add, and a second pass over its own output adds nothing. A part with no entry
for the neighbour is left alone -- that layout was never saved with it, and
hello_imgui will build it from code as it would have anyway.

This is hello_imgui's file and its format is not a published one. Everything read
here is ``[Window][label]`` blocks and ``DockId=0x<id>,<order>`` lines, which are
Dear ImGui's own window settings; the part markers are hello_imgui's. If either
changes shape nothing matches, nothing is written, and the cost is the rebuild
described above -- the behaviour without this module.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

# One layout's Dear ImGui settings start at this marker and run to the next marker
# of any kind.
_PART = re.compile(r"^;;;<<<(?P<name>[^>\n]*)>>>;;;[ \t]*\r?$", re.MULTILINE)
_IMGUI_PART = "ImGui_"
_DOCK = re.compile(r"^DockId=(?P<node>0x[0-9A-Fa-f]+),(?P<order>-?\d+)[ \t]*\r?$", re.MULTILINE)


def _block(part: str, label: str) -> re.Match[str] | None:
    """
    The ``[Window][label]`` entry of one part: its header and the lines under it,
    up to and including the blank line that ends it.
    """
    return re.search(
        rf"^\[Window\]\[{re.escape(label)}\][ \t]*\r?\n"
        r"(?P<body>(?:[^\[\r\n][^\n]*(?:\n|\Z))*)(?:\r?\n)?",
        part,
        re.MULTILINE,
    )


def _with_entry(part: str, new: str, beside: str) -> str:
    if _block(part, new) is not None:
        return part
    neighbour = _block(part, beside)
    if neighbour is None:
        return part
    newline = "\r\n" if "\r\n" in neighbour.group(0) else "\n"
    lines = [f"[Window][{new}]", "Collapsed=0"]
    docked = _DOCK.search(neighbour.group("body"))
    if docked is not None:
        node = docked.group("node")
        # Past every tab already in the node, so no saved order is contested and
        # none has to be rewritten to make room.
        last = max(
            int(m.group("order"))
            for m in _DOCK.finditer(part)
            if int(m.group("node"), 16) == int(node, 16)
        )
        lines.append(f"DockId={node},{last + 1}")
    entry = newline.join(lines) + newline + newline
    at = neighbour.end()
    # The neighbour's entry is the last thing in the part and nothing blank
    # follows it: end its last line and leave the blank line an entry is closed by.
    lead = ""
    if not neighbour.group(0).endswith(("\n\n", "\n\r\n")):
        lead = newline if neighbour.group(0).endswith("\n") else newline + newline
    return part[:at] + lead + entry + part[at:]


def with_window_beside(text: str, new: str, beside: str) -> str:
    """
    ``text`` with a ``[Window][new]`` entry in every saved layout that has one for
    ``beside`` and none for ``new``, docked in ``beside``'s node as its last tab.

    Where ``beside`` is saved floating the entry carries no dock, and the new pane
    floats too: the entry is what keeps the layout, and there is no node to name.
    """
    marks = list(_PART.finditer(text))
    out: list[str] = []
    cursor = 0
    for index, mark in enumerate(marks):
        if not mark.group("name").startswith(_IMGUI_PART):
            continue
        start = mark.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        out.append(text[cursor:start])
        out.append(_with_entry(text[start:end], new, beside))
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def keep_layout_for(path: Path, new: str, beside: str) -> str | None:
    """
    Apply ``with_window_beside`` to the ini at ``path``, before the runner reads it.

    Returns what was done, for the log, or None when there was nothing to do: no
    file yet, or every saved layout already knows ``new``. Raises ``OSError`` or
    ``UnicodeDecodeError`` when the file could not be read or replaced; the caller
    decides that the app starts anyway.

    Replaced by rename, so a crash part-way leaves the old file whole and not a
    truncated one hello_imgui would read as no layout at all.
    """
    try:
        # newline="" so the file's own line endings are what is written back.
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
    except FileNotFoundError:
        return None
    patched = with_window_beside(text, new, beside)
    if patched == text:
        return None
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(patched)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    added = patched.count(f"[Window][{new}]") - text.count(f"[Window][{new}]")
    return f"{new} added beside {beside} in {added} saved layout(s) of {path}"
