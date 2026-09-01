"""
Resumable sessions: what is on disk, plus the little pptmstr adds on top.

Two sources, and the split is the whole point of the module.

``list_sessions()`` from the SDK is the source of truth for *what exists*. An index
this app maintained would have the same failure mode as this app: if pptmstr dies
mid-session its entry is missing or stale, and that is exactly the case a resume
picker exists to recover from. Transcripts are written by the CLI and survive us,
and they include sessions that predate this feature entirely.

The local overlay carries only what ``list_sessions`` cannot know -- a bookmark flag
and an operator-supplied title, keyed by session id. It is additive: an overlay
entry is never evidence that a session exists, only decoration for one that does.

Attribution is imperfect and this module does not pretend otherwise. ``SDKSessionInfo``
has no producer field, so nothing here can tell a pptmstr session from one the
operator started at a terminal. ``tag`` is the only hook and it only helps sessions
tagged from now on, so it is passed through for a caller to mark what it recognises.
Nothing is filtered out on that basis: the operator loses their own Claude Code
sessions too, and this is how they get them back.

Blocking. ``list_sessions`` opens, stats and head/tail-reads every transcript file
and shells out to ``git worktree``; against this repo's ~130 transcripts it takes
tens of milliseconds warm and is unbounded cold. It must not be called from a draw
call. Nothing here starts a thread. What the split between ``enumerate_sessions``
(does the IO) and ``merge_overlay`` (pure) buys is that all of the blocking lives
behind one name, so what a caller has to get onto a worker is a single function --
and that the join, the ordering and the overlay rules are testable without a
transcript tree.

The split is not a caching hook, and a caller should not treat it as one.
``enumerate_sessions`` returns ``SessionRow`` while ``merge_overlay`` takes
``SDKSessionInfo``, so holding a listing to re-merge it means calling
``list_sessions`` directly and giving up ``enumerate_sessions``' refusal to turn a
listing error into an empty picker. A caller that wants a bookmark toggle to cost
nothing caches the merged rows instead and rewrites the single row it changed --
``set_bookmark`` on the overlay, ``dataclasses.replace`` on the row. That is cheaper
than a re-merge and, unlike one, it leaves the order the operator is reading alone.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from claude_agent_sdk import SDKSessionInfo, list_sessions

from pptmstr.settings import config_dir


@dataclass(frozen=True, slots=True)
class SessionMark:
    """
    The overlay's record for one session. Both fields absent means no record.
    """

    bookmarked: bool = False
    title: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.bookmarked and self.title is None


# Keyed by session id. Sessions the operator has never touched are simply absent.
Overlay = Mapping[str, SessionMark]


@dataclass(frozen=True, slots=True)
class SessionRow:
    """
    One resumable session, as a picker needs it.

    ``created_at`` and ``last_modified`` are **epoch milliseconds**, not seconds --
    the SDK builds them as ``int(st_mtime * 1000)`` and
    ``int(datetime.fromisoformat(ts).timestamp() * 1000)``. A caller computing "how
    long ago" against ``time.time()`` has to divide, and getting it wrong yields ages
    fifty thousand years off rather than an obvious error.

    ``summary`` is the SDK's own one-line description and is what the CLI shows.
    ``SDKSessionInfo.custom_title`` is deliberately not carried: across all 167
    transcripts on this machine it is either ``None`` or byte-identical to
    ``summary``, so it holds nothing ``summary`` does not.
    """

    session_id: str
    cwd: str | None
    git_branch: str | None
    summary: str
    first_prompt: str | None
    created_at: int | None
    last_modified: int
    tag: str | None
    bookmarked: bool = False
    title: str | None = None

    @property
    def label(self) -> str:
        """
        The identifying line for a human. An id alone is useless to one.

        Derived rather than stored so the precedence lives in one place: an operator
        title outranks the SDK's summary, which outranks the first prompt, and the
        id is the last resort for a transcript too damaged to yield any of them.
        """
        for candidate in (self.title, self.summary, self.first_prompt):
            if candidate:
                return candidate
        return self.session_id


class Lister(Protocol):
    """
    The shape of ``claude_agent_sdk.list_sessions`` this module depends on.

    Injected rather than reached for, so tests never touch the real transcript tree
    and a caller can supply a cached or pre-filtered listing.
    """

    def __call__(self, directory: str | None = ...) -> Sequence[SDKSessionInfo]: ...


# ---------------------------------------------------------------------------
# The overlay: load, save, and the two edits it supports
# ---------------------------------------------------------------------------


def overlay_path() -> Path:
    return config_dir() / "sessions.json"


def load_overlay(path: Path | None = None) -> Overlay:
    """
    Read the overlay, falling back to empty on anything unreadable.

    A corrupt or hand-edited overlay must not stop the session list rendering: the
    list's value is recovering lost work, and losing a bookmark is a nuisance next
    to not being offered the session at all. Malformed entries are skipped
    individually rather than condemning the file, so one bad record does not take
    the operator's other bookmarks with it.
    """
    target = path or overlay_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries = raw.get("sessions")
    if not isinstance(entries, dict):
        return {}

    marks: dict[str, SessionMark] = {}
    for session_id, value in entries.items():
        if not isinstance(session_id, str) or not isinstance(value, dict):
            continue
        bookmarked = value.get("bookmarked", False)
        title = value.get("title")
        # Guard what the constructor would otherwise accept silently. A non-bool in
        # bookmarked would survive into a sort key, and a non-str title would fail
        # much later inside a text-drawing call.
        if not isinstance(bookmarked, bool):
            continue
        if title is not None and not isinstance(title, str):
            continue
        mark = SessionMark(bookmarked=bookmarked, title=title)
        if not mark.is_empty:
            marks[session_id] = mark
    return marks


def save_overlay(overlay: Overlay, path: Path | None = None) -> None:
    """
    Write the overlay atomically.

    Temporary file in the same directory, renamed over the target, so an interrupted
    write leaves the previous overlay intact rather than a truncated one. rename(2)
    is atomic only within a filesystem, which is why the temp file cannot go in /tmp.

    Failures are swallowed: this is called when the operator clicks a bookmark, and a
    read-only config directory must not take down a frame.
    """
    target = path or overlay_path()
    entries = {
        session_id: {"bookmarked": mark.bookmarked, "title": mark.title}
        for session_id, mark in overlay.items()
        if not mark.is_empty
    }
    payload: dict[str, Any] = {"sessions": entries}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".sessions-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError:
        return


def _amended(overlay: Overlay, session_id: str, mark: SessionMark) -> Overlay:
    updated = dict(overlay)
    if mark.is_empty:
        updated.pop(session_id, None)
    else:
        updated[session_id] = mark
    return updated


def set_bookmark(overlay: Overlay, session_id: str, bookmarked: bool) -> Overlay:
    """
    Return an overlay with the bookmark set or cleared. Does not write.
    """
    current = overlay.get(session_id, SessionMark())
    return _amended(overlay, session_id, replace(current, bookmarked=bookmarked))


def set_title(overlay: Overlay, session_id: str, title: str | None) -> Overlay:
    """
    Return an overlay with the operator's title set, or cleared by ``None``.

    An empty string clears too. A title that renders as nothing is not a title, and
    keeping one would leave a row whose label silently fell through to the summary
    while the overlay still claimed a title was set.
    """
    current = overlay.get(session_id, SessionMark())
    return _amended(overlay, session_id, replace(current, title=title or None))


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def merge_overlay(
    infos: Iterable[SDKSessionInfo],
    overlay: Overlay | None = None,
    *,
    bookmarked_first: bool = False,
) -> tuple[SessionRow, ...]:
    """
    Join the listing with the overlay, most-recent-first. Pure; no IO.

    Iteration is over ``infos``, never over the overlay, which is what makes an
    overlay entry for a deleted session harmless: it decorates nothing and produces
    no row. Such entries are deliberately *not* pruned here -- a listing narrowed to
    one project omits every session in every other one, and deleting bookmarks
    because the current filter did not mention them would destroy them.

    The sort is done here rather than trusted from the lister. ``list_sessions``
    documents last-modified-descending order, but the ordering is this function's
    guarantee to its caller and a cached or hand-built listing need not have it.
    """
    marks = overlay or {}
    rows = [
        SessionRow(
            session_id=info.session_id,
            cwd=info.cwd,
            git_branch=info.git_branch,
            summary=info.summary,
            first_prompt=info.first_prompt,
            created_at=info.created_at,
            last_modified=info.last_modified,
            tag=info.tag,
            bookmarked=marks.get(info.session_id, SessionMark()).bookmarked,
            title=marks.get(info.session_id, SessionMark()).title,
        )
        for info in infos
    ]
    if bookmarked_first:
        rows.sort(key=lambda row: (not row.bookmarked, -row.last_modified))
    else:
        rows.sort(key=lambda row: -row.last_modified)
    return tuple(rows)


def enumerate_sessions(
    *,
    cwd: str | None = None,
    overlay: Overlay | None = None,
    bookmarked_first: bool = False,
    lister: Lister = list_sessions,
) -> tuple[SessionRow, ...]:
    """
    Scan the transcript tree and return rows, most-recent-first.

    **Blocking.** Call it off the draw thread and cache what it returns.

    ``cwd`` is passed to the SDK as ``directory``, which scans that one project
    directory instead of every project -- the difference is the point of doing it
    here rather than filtering afterwards. The SDK also folds in the directory's git
    worktrees, so a returned row's own ``cwd`` may differ from the one asked for.
    That is wanted for a picker: a worktree of this repo is still this work.
    ``same_cwd`` is the strict version, for narrowing a listing already in hand.

    Errors from the lister are not caught. An unreadable transcript tree returning
    "no sessions" is indistinguishable from having none, and quietly showing an empty
    picker to an operator hunting for lost work is the worst available outcome.
    """
    return merge_overlay(lister(directory=cwd), overlay, bookmarked_first=bookmarked_first)


def same_cwd(rows: Iterable[SessionRow], cwd: str | None) -> tuple[SessionRow, ...]:
    """
    Narrow rows to one working directory, preserving order.

    Exact string equality against ``SessionRow.cwd``, which is the absolute path the
    CLI recorded. Nothing is resolved, expanded or normalised here, so a **relative**
    path matches nothing whatever the tree holds: ``same_cwd(rows, ".")`` is ``()``,
    and the launcher's ``cwd`` defaults to exactly that. Nothing failed on that route,
    so ``enumerate_sessions``' refusal to turn an error into an empty picker does not
    guard it -- the result reads as "you have no sessions" and is a path-shape
    mismatch. Callers pass an absolute path.

    It is also stricter than ``enumerate_sessions(cwd=...)`` a second way: the SDK
    folds a directory's git worktrees into its listing and this does not, so a row
    recorded in a worktree of the given directory is dropped. The two are not
    interchangeable and are not meant to be. This one narrows a listing already in
    hand without paying for another scan; the SDK-backed filter answers "what work
    happened on this project", worktrees included.
    """
    return tuple(row for row in rows if row.cwd == cwd)


def bookmarked(rows: Iterable[SessionRow]) -> tuple[SessionRow, ...]:
    """
    Just the bookmarked rows, preserving order.
    """
    return tuple(row for row in rows if row.bookmarked)
