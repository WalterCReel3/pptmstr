"""
BRIEF: the premises a session was launched with, and everything that amended them.

**The pane exists because the log is only half the fix.** Premises on disk that
nobody renders are premises the operator cannot check, and the operator is the one
participant who can tell that an amendment was wrong. A worker reads the directory;
this is the same directory, derived the same way, for the person who wrote it.

**Superseded entries stay on screen.** That is obligation 1 of the record and the
reason the derivation exists: rendering only current state would make an overturned
premise silently vanish, so the log would exist and nobody reading it could tell
anything had changed. An overturned premise is shown struck through in the reader's
attention rather than removed, labelled with the entry that overturned it.

**With the typeset seam on, an entry is one held view: its heading and its body.**
One ``typeset.document`` per entry rather than one over the whole brief, because
each entry is followed by a checkbox and a widget cannot sit inside a view. The
price is that a selection stops at an entry's edge. The views are uncapped: the
pane's own child scrolls, each view culls to it, and a second scrollbar inside an
entry would hide premises behind a gesture. The child scrolls sideways too, since a
fenced line is never broken and one longer than the pane has to be reachable. The
heading is a part of the view, so it wraps at the pane's width and is copied with
the body it heads.

**Nothing in a body outranks the line that heads its entry.** The entry's own line
is set as a second-level heading and every heading in the body ``_BODY_DEMOTE``
levels down, so a body's ``#`` is one size under it and anything deeper is bold at
the body's size. A body that opens with a title would otherwise read as the start
of the entry and its "000 premises" as a caption.

**An overturned entry is told apart by its heading's words, and dimmed as well.**
"-- superseded by 003" is what says it; the dim ink is only a second cue, since
contrast alone is not something every reader or every display carries. The library
has no strike-through to add.

**A body is not assumed to be markdown.** An entry is whatever an operator or a
lead wrote, and ``typeset.prose_parts`` is the rule for reading it, the same one
every pane showing a body applies: one item per line stays one item per line.

**Appending is the only write, and it never edits.** There is no "edit entry" here
and there will not be one: free mutation is what destroys the forensic value of
putting premises on disk at all -- *what did this worker act on* and *what did the
file end up saying* differ in exactly the session that went wrong.

Follows the cursor, like DETAIL, CONTEXT and BOARD. Absent for a session launched
without a brief, which is most of them.

The directory is read through a memo keyed on its modification time. Reading it per
frame would put an ``iterdir`` and N ``read_text`` calls on the frame path at 60fps,
and the frame loop's whole premise is that building a frame touches no IO.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from imgui_bundle import imgui

from .. import brief
from ..model import Snapshot
from ..theme import P
from . import typeset
from .focus import FocusState
from .widgets import CTRL_ENTER_SUBMITS, multiline_input, section

# One entry's body in the pane. Generous -- premises are the thing this surface
# exists to show -- and announced when it bites, like every other bound here.
_MAX_BODY_CHARS = 6000

# The level an entry's own line is set at, and how far down that puts a heading in
# its body: a body's ``#`` lands one level under the entry's line.
_ENTRY_LEVEL = 2
_BODY_DEMOTE = _ENTRY_LEVEL


@dataclass
class BriefState:
    """
    What the operator is part-way through appending, and the last directory read.

    The draft is here rather than in the store for the reason ``ReviewState`` gives:
    a half-written premise is not a fact about the world, and an append abandoned
    mid-sentence must leave nothing behind.
    """

    draft: str = ""
    # Ordinals the draft will declare it overturns, toggled on the entries above it.
    supersedes: set[int] = field(default_factory=set)
    # (directory, entry filenames) the memo below was built from. None until read.
    _key: tuple[str, tuple[str, ...]] | None = None
    _derived: tuple[brief.DerivedEntry, ...] = ()
    # The typeset view of each entry, and the parts each was built from, both by
    # the entry's path: unique where an ordinal written by hand need not be, and
    # different in another directory. The parts are held because the seam compares
    # nothing for a sequence it was handed last frame.
    views: typeset.ViewCache = field(default_factory=typeset.ViewCache)
    _parts: dict[str, tuple[typeset.Part, ...]] = field(default_factory=dict)
    # Set when a write failed, and shown until the next successful one. A failed
    # append is invisible otherwise -- the operator would believe a premise landed.
    error: str | None = None

    def entries(self, directory: Path) -> tuple[brief.DerivedEntry, ...]:
        """
        The derived brief, re-read only when the directory has changed.

        **Keyed on the filenames, not on the directory's mtime.** Mtime was the
        obvious key and it is wrong: measured here, two appends in quick succession
        leave ``st_mtime_ns`` *identical* -- the delta is zero -- so an entry
        written within a tick of the last read would never be picked up. In a
        60fps pane that is not a rare race, and it fails silently: the operator
        appends, sees nothing, and appends again.

        Filenames are a complete key because entries are append-only and never
        edited, so a new entry always brings a new name. It costs one ``iterdir``
        per frame, which is the cheap half -- what the memo exists to avoid is the
        N ``read_text`` calls behind it.

        Keyed on the directory's own contents rather than on a flag this pane sets,
        so a write it did not make still invalidates it.
        """
        try:
            names = tuple(sorted(p.name for p in directory.iterdir()))
        except OSError:
            self._key, self._derived = None, ()
            self._hold(())
            return ()
        key = (str(directory), names)
        if key != self._key:
            self._key = key
            self._derived = brief.derive(brief.read_entries(directory))
            self._hold(self._derived)
        return self._derived

    def _hold(self, derived: tuple[brief.DerivedEntry, ...]) -> None:
        """
        Build the parts for what is now on disk and let go of every other view.

        An entry that was just overturned gets new parts with the same text, so its
        view is re-inked and not laid out again.
        """
        self._parts = {str(item.entry.path): entry_parts(item) for item in derived}
        self.views.prune(self._parts.keys())

    def parts(self, item: brief.DerivedEntry) -> tuple[typeset.Part, ...]:
        """
        The parts of one entry this state last read: the same object every frame.
        """
        return self._parts[str(item.entry.path)]

    def invalidate(self) -> None:
        """Force the next read. Called after a write, whose mtime may match."""
        self._key = None


def draw(snap: Snapshot, focus: FocusState, pane: BriefState) -> None:
    """Render the brief of the session under the cursor. Never reads the store."""
    node = focus.node(snap)
    record = snap.nodes.get((node[0], None)) if node is not None else None
    if record is None or not record.brief:
        imgui.text_disabled("this session was launched without a brief")
        return

    directory = Path(record.brief)
    derived = pane.entries(directory)
    _summary(derived, directory)

    typeset_on = typeset.enabled()
    # A fenced line is never broken, so under the seam the child scrolls sideways to
    # the end of one. A window that scrolls sideways wraps native text at its widest
    # content rather than at its edge, hence the explicit wrap position.
    flags = imgui.WindowFlags_.horizontal_scrollbar.value if typeset_on else 0
    if imgui.begin_child("##brief", window_flags=flags):
        wrap_at = 0.0
        if typeset_on:
            wrap_at = imgui.get_cursor_pos_x() + imgui.get_content_region_avail().x
        imgui.push_text_wrap_pos(wrap_at)
        if not derived:
            imgui.text_colored(
                P.text_dim.vec4, "no premises written yet -- append the first one below"
            )
        for item in derived:
            _entry(item, pane)
        _composer(pane, directory)
        imgui.pop_text_wrap_pos()
    imgui.end_child()


def _summary(derived: tuple[brief.DerivedEntry, ...], directory: Path) -> None:
    imgui.push_text_wrap_pos(0.0)
    imgui.text_colored(P.text_dim.vec4, summary_label(derived))
    # The path, because a worker is told to read this directory and the operator has
    # to be able to check that the two are the same place.
    imgui.text_colored(P.text_dim.vec4, str(directory))
    imgui.pop_text_wrap_pos()


def _entry(item: brief.DerivedEntry, pane: BriefState) -> None:
    entry = item.entry
    imgui.spacing()
    body, dropped = clip(entry.body, _MAX_BODY_CHARS)
    if typeset.enabled():
        typeset.document(pane.views, str(entry.path), pane.parts(item))
    else:
        imgui.text_colored((P.text_dim if item.is_superseded else P.accent).vec4, entry_label(item))
        # A superseded premise is dimmed rather than hidden: still readable, visibly
        # not current. Removing it is what obligation 1 forbids.
        imgui.text_colored((P.text_dim if item.is_superseded else P.text).vec4, body)
    if dropped:
        imgui.text_colored(P.text_dim.vec4, f"... {dropped} more character(s) not shown")

    marked = entry.ordinal in pane.supersedes
    changed, marked = imgui.checkbox(f"the entry below supersedes this##{entry.ordinal}", marked)
    if changed:
        pane.supersedes.symmetric_difference_update({entry.ordinal})


def _composer(pane: BriefState, directory: Path) -> None:
    section("append")
    submitted, pane.draft = multiline_input(
        "##brief-draft",
        pane.draft,
        imgui.ImVec2(-1, 120.0),
        wrap=True,
        flags=CTRL_ENTER_SUBMITS,
    )
    imgui.text_disabled(compose_hint(pane.supersedes))

    if pane.error:
        imgui.text_colored(P.danger.vec4, pane.error)

    ready = bool(pane.draft.strip())
    if not ready:
        imgui.begin_disabled()
    clicked = imgui.button("append", imgui.ImVec2(110.0, 0.0))
    if not ready:
        imgui.end_disabled()

    if ready and (clicked or submitted):
        _append(pane, directory)


def _append(pane: BriefState, directory: Path) -> None:
    """
    Write the draft as the next entry.

    The failure is surfaced rather than swallowed, which is why ``brief.write_entry``
    raises where ``settings.save`` returns: a premise the operator believes landed
    and did not is worse than no premise, because a worker acts on its absence while
    the operator acts on its presence.
    """
    try:
        brief.write_entry(directory, pane.draft, supersedes=tuple(sorted(pane.supersedes)))
    except OSError as exc:
        pane.error = f"could not write the entry: {exc}"
        return
    pane.error = None
    pane.draft = ""
    pane.supersedes.clear()
    pane.invalidate()


# -- pure helpers, testable without a GL context -------------------------------


def summary_label(derived: tuple[brief.DerivedEntry, ...]) -> str:
    """
    The shape of the brief in one line.

    Counts what is still standing separately from the total, because those are the
    two different questions an operator arrives with: how much has been said, and
    how much of it is still true.
    """
    if not derived:
        return "no entries"
    standing = sum(1 for d in derived if not d.is_superseded)
    superseded = len(derived) - standing
    if not superseded:
        return f"{len(derived)} entr(ies)"
    return f"{len(derived)} entr(ies) · {standing} standing · {superseded} superseded"


def entry_label(item: brief.DerivedEntry) -> str:
    """
    One entry's heading, carrying its own fate.

    "000 premises -- superseded by 003" rather than dropping the entry, which is
    obligation 1 in the one place a reader actually looks.
    """
    entry = item.entry
    head = f"{entry.ordinal:03d} {entry.kind}"
    if entry.supersedes:
        head += f" · overturns {', '.join(f'{o:03d}' for o in entry.supersedes)}"
    if item.is_superseded:
        head += f" -- superseded by {', '.join(f'{o:03d}' for o in item.superseded_by)}"
    return head


def entry_parts(item: brief.DerivedEntry) -> tuple[typeset.Part, ...]:
    """
    One entry as the seam sets it: its heading, then its body under that heading.

    The heading is written as a markdown heading unescaped. ``entry_label`` is built
    from ordinals and a kind ``brief`` only accepts as ``[a-z0-9-]+``, so it holds
    nothing markdown reads.

    The inks are named by role, so a theme switch re-inks parts that are held.
    """
    body, _ = clip(item.entry.body, _MAX_BODY_CHARS)
    dim = "text_dim" if item.is_superseded else None
    return (
        typeset.Part(f"{'#' * _ENTRY_LEVEL} {entry_label(item)}", colour=dim or "accent"),
        *(replace(part, demote=_BODY_DEMOTE) for part in typeset.prose_parts(body, colour=dim)),
    )


def compose_hint(supersedes: set[int]) -> str:
    """
    What the append will do, said before it happens.

    Names the ordinals rather than a count: "supersedes 2 entries" is a sentence an
    operator cannot check against the boxes they ticked.
    """
    if not supersedes:
        return "Ctrl+Enter appends. Nothing is overturned unless you tick an entry above."
    marked = ", ".join(f"{o:03d}" for o in sorted(supersedes))
    return f"Ctrl+Enter appends, overturning {marked}."


def clip(text: str, limit: int) -> tuple[str, int]:
    """Bound a body, reporting what was dropped rather than eliding it silently."""
    if len(text) <= limit:
        return text, 0
    return text[:limit], len(text) - limit
