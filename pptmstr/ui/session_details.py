"""
Details: what pptmstr holds about one session or one sub-agent, a value to a line,
each one copyable.

The facts already exist and are scattered: the record has the directory, the model
and the spend; the session object has the gate it is answering with and whether it
is contained; the board log's state is the board pane's; the transcript is the
CLI's. This puts them in one place and says where each came from.

**A value pptmstr does not hold is "not known", with the reason, and is never
filled in.** A closed session has no session object to ask, a sub-agent has no
context window of its own, and nothing records when an agent last did anything. A
plausible value in any of those rows would be read as a fact.

**Three sources, and they are kept apart.** ``view`` is pure and is rebuilt every
frame from the snapshot, so the state, the spend and the gate are this frame's.
``Held`` is what the session object says, read by the caller on the frame.
``look`` is the only thing here that touches the disk, and it runs once, when the
modal is opened: a transcript's existence and a directory's git layout are worth
one read at a click and not one per frame.

**A session recalled from its file was observed by nothing here**
(``AgentRecord.recalled``). Its record is the field defaults -- done, zero spend,
no sub-agents -- and none of those is drawn. Each of its rows is what the session
file recorded and says that it is, or is "not known".
"""

from __future__ import annotations

import dataclasses
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from claude_agent_sdk import project_key_for_directory
from imgui_bundle import imgui

from .. import board as board_mod
from .. import boardlog, boardsync, sessionfile
from .. import brief as brief_mod
from ..approval import Policy
from ..model import (
    AgentRecord,
    AgentState,
    EndedHow,
    NodeId,
    Recalled,
    Snapshot,
    TranscriptPresence,
)
from ..theme import STATE_LABEL, P
from . import board_pane, launcher
from .widgets import author_text, flat_line, format_elapsed, one_line

TITLE = "Session details###session-details"
NOT_KNOWN = "not known"

_WIDTH = 760.0
_LABEL_W = 150.0
_COPY_W = 44.0


@dataclass(frozen=True, slots=True)
class Row:
    """
    One line: what it is, its value, and what the copy control puts on the
    clipboard -- None for a row with nothing to copy, which is every row whose
    value is not known.

    ``copy`` is the value itself and never the drawn line: a path is copied as the
    path, without the words around it.
    """

    label: str
    value: str
    copy: str | None = None
    note: str = ""
    known: bool = True


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    rows: tuple[Row, ...]


@dataclass(frozen=True, slots=True)
class View:
    title: str
    sections: tuple[Section, ...]

    def row(self, label: str) -> Row | None:
        """The row with this label, for a reader that wants one value."""
        return next((r for s in self.sections for r in s.rows if r.label == label), None)


@dataclass(frozen=True, slots=True)
class Held:
    """
    What the session object behind a card says on this frame.

    ``running`` is whether the pool holds it, which is whether ``policy`` is being
    applied to anything: a session the pool has let go keeps the attribute and
    gates nothing with it. ``transcript`` is the path a hook of the CLI's carried,
    or None before the first one.
    """

    policy: Policy
    contained: bool
    transcript: str | None
    running: bool


def is_contained(settings: str | None) -> bool:
    """
    Whether a CLI process started with this settings string was started with
    containment: the string has something in it besides whitespace.

    ``AgentSession._unpaired``'s test, spelled again so the card's menu and this
    modal ask one function and neither imports the driver. That method says why
    a blank string is absent. A test holds the two to one answer.
    """
    return bool((settings or "").strip())


@dataclass(frozen=True, slots=True)
class Looked:
    """
    What one look at the disk found, when the modal was opened.

    ``git`` is ``launcher.git_layout`` of the directory, meaningful only where
    ``git_read`` is set -- None is one of its answers. ``transcript`` is where the
    CLI's naming puts the session's transcript, and ``transcript_there`` whether a
    file is there. ``agent_transcript`` is the same for a sub-agent, directly under
    the session's ``subagents`` directory.

    ``session_dir`` is where pptmstr keeps what it knows about the session,
    ``session_file`` the file in it and ``board_dir`` the board log in it; all
    three are None for an id that names no directory, and meaningful only where
    ``files_read`` is set. ``board_legacy`` is the session's directory under the
    legacy root when records of it are there, as ``boardsync.find`` judges that,
    and ``board_split`` whether its own directory holds records as well.
    """

    git_read: bool = False
    git: launcher.GitLayout = None
    transcript: str | None = None
    transcript_there: bool = False
    agent_transcript: str | None = None
    agent_transcript_there: bool = False
    files_read: bool = False
    session_dir: str | None = None
    session_file: str | None = None
    session_file_there: bool = False
    board_dir: str | None = None
    board_legacy: str | None = None
    board_split: bool = False


@dataclass
class DetailsState:
    """
    Which node the modal is for, and what was read for it when it opened.
    """

    node: NodeId | None = None
    looked: Looked | None = None
    # Asked for and not yet opened: the next ``draw`` opens the popup.
    _opening: bool = False
    # How many frames it has been drawn for. Escape is read from the second.
    _frames: int = 0

    def ask(self, node: NodeId) -> None:
        self.node = node
        self.looked = None
        self._opening = True
        self._frames = 0

    def close(self) -> None:
        self.node = None
        self.looked = None
        self._opening = False
        self._frames = 0

    def prune(self, snap: Snapshot) -> None:
        if self.node is not None and self.node not in snap.nodes:
            self.close()


def transcript_for(cwd: str, session_id: str) -> Path:
    """
    Where the CLI's naming puts a session's transcript:
    ``<projects>/<project key>/<session id>.jsonl``.

    The key is the SDK's own (``project_key_for_directory``), which resolves the
    directory -- so this is IO, and is called from ``look``. The projects directory
    is ``brief.default_root``, the one a brief is written beside its transcript
    under.
    """
    return brief_mod.default_root() / project_key_for_directory(cwd) / f"{session_id}.jsonl"


def look(rec: AgentRecord, reported: str | None) -> Looked:
    """
    The disk half, once, at the click that opens the modal. **Blocking**: a walk up
    one path, a ``realpath``, a handful of ``stat`` calls and, for a session, two
    directory listings. No file is read.

    ``reported`` is the transcript path the session's CLI gave, when it has given
    one. It is preferred to the derived one for finding a sub-agent's transcript,
    because it is where the file is and not where the naming says it should be.

    A record with no directory is not looked for under one; where pptmstr keeps
    its own files for the session does not depend on it.
    """
    session_id, agent_id = rec.node_id
    looked = Looked() if agent_id is not None else _session_files(session_id)
    if not rec.cwd:
        return looked
    transcript = transcript_for(rec.cwd, session_id)
    agent_transcript: Path | None = None
    if agent_id is not None:
        base = Path(reported) if reported else transcript
        agent_transcript = base.with_suffix("") / "subagents" / f"agent-{agent_id}.jsonl"
    return dataclasses.replace(
        looked,
        git_read=True,
        git=launcher.git_layout(rec.cwd),
        transcript=str(transcript),
        transcript_there=transcript.is_file(),
        agent_transcript=None if agent_transcript is None else str(agent_transcript),
        agent_transcript_there=agent_transcript is not None and agent_transcript.is_file(),
    )


def _session_files(session_id: str) -> Looked:
    """
    Where pptmstr keeps its own files for a session, and whether records of its
    board are under the legacy root as well.

    The second is ``boardsync.find``'s question, answered from two listings
    where ``find`` reads the log: this runs on the thread that draws, which
    ``find`` must not. The legacy directory counts when it is another directory
    than the session's own and its listing shows a record, or could not be
    taken. A test holds this to ``find``'s answer.
    """
    directory = sessionfile.session_dir(session_id)
    file = sessionfile.file_path(session_id)
    board = sessionfile.board_dir(session_id)
    if directory is None or file is None or board is None:
        return Looked(files_read=True)
    legacy: Path | None = boardlog.session_dir(boardsync.legacy_root(), session_id)
    if legacy is not None:
        try:
            same = os.path.samefile(legacy, board)
        except OSError:
            same = False
        if same or boardlog.next_ordinal(legacy) == 0:
            legacy = None
    return Looked(
        files_read=True,
        session_dir=str(directory),
        session_file=str(file),
        session_file_there=file.is_file(),
        board_dir=str(board),
        board_legacy=None if legacy is None else str(legacy),
        board_split=legacy is not None and boardlog.next_ordinal(board) != 0,
    )


def _known(label: str, value: str, *, copy: str | None = None, note: str = "") -> Row:
    """A row whose value is held. It copies as ``copy``, or as the value drawn."""
    return Row(label, value, copy=value if copy is None else copy, note=note)


def _stated(label: str, value: str, note: str = "") -> Row:
    """A row that is a known fact with nothing worth copying: "none", "no"."""
    return Row(label, value, note=note)


def _unknown(label: str, why: str) -> Row:
    return Row(label, NOT_KNOWN, note=why, known=False)


def _stamp(at: float, now: float, wall_now: float) -> str:
    """
    A monotonic stamp as a time on the wall clock, with how long ago beside it.

    The store's stamps count from boot. The difference from ``now`` is exact; the
    wall time is that difference taken from the wall clock's own reading, so it is
    as right as the wall clock has stayed since.
    """
    ago = max(0.0, now - at)
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(wall_now - ago))
    return f"{when} ({format_elapsed(ago)} ago)"


def _wall_stamp(at: float, wall_now: float) -> str:
    """
    A wall-clock instant, with how long before ``wall_now`` it was. Both are
    wall-clock seconds, which is what a session file holds; an instant ahead of
    the clock is a clock that was moved, and reads as no time ago.
    """
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(at))
    return f"{when} ({format_elapsed(max(0.0, wall_now - at))} ago)"


def _tokens(count: int) -> str:
    return f"{count:,}"


# Said of every row a recalled session has no value for because nothing ran.
_NOT_RUN = "it has not run in this window, and its session file does not record this"
_FROM_FILE = "as its session file last recorded"

_ENDED = {
    EndedHow.CLOSED: "closed",
    EndedHow.FAILED: "failed",
    EndedHow.SHUTDOWN: "at shutdown",
}


TITLE_IS_LABEL = (
    "the CLI's label for the conversation; its original task is not recorded, so "
    "Fork and Retry cannot start from it"
)


def _title_row(rec: AgentRecord) -> Row:
    """
    What a session is called: its task, or -- for one whose original task
    nothing recorded -- the label it is titled by, under that name and with the
    reason the card's Fork and Retry are greyed
    (``AgentRecord.titled_by_label``). The same before and after a resume.
    """
    if rec.titled_by_label:
        return _known("title", flat_line(rec.task), copy=rec.task, note=TITLE_IS_LABEL)
    return _known("task", flat_line(rec.task), copy=rec.task)


def failed_then_closed(recalled: Recalled) -> bool:
    """
    Whether the file describes a session that failed and was closed afterwards:
    an end recorded as closed with a failure's text still beside it. Closing a
    failed card changes how its end is described and keeps what the failure
    said (``fleetsync``), and any other close records no error at all.
    """
    return recalled.ended_how is EndedHow.CLOSED and bool(recalled.error)


def ended_text(recalled: Recalled) -> str:
    """How the file says the last run ended. ``ended_how`` must not be None."""
    assert recalled.ended_how is not None
    if failed_then_closed(recalled):
        return "closed, after it had failed"
    return _ENDED[recalled.ended_how]


def _state_row(rec: AgentRecord) -> Row:
    if rec.recalled is not None:
        return _stated("state", "closed", "recalled from its session file; no process is behind it")
    topic = flat_line(rec.topic)
    return _stated("state", STATE_LABEL[rec.state] + (f" - {topic}" if topic else ""))


def _recalled_time_rows(recalled: Recalled, wall_now: float) -> list[Row]:
    """
    A recalled session's times: the session file's, which are wall-clock. The
    record's own stamps are zero and are not read.
    """
    rows: list[Row] = []
    if recalled.created_at is not None and recalled.task_recorded is False:
        # The file was begun by a resume: the session is older than its file,
        # and this is when a build that writes one first ran it.
        rows.append(
            _stated(
                "first seen",
                _wall_stamp(recalled.created_at, wall_now),
                "when this build first ran it, "
                + _FROM_FILE
                + "; the session began earlier, and when is not recorded",
            )
        )
    elif recalled.created_at is not None:
        rows.append(
            _stated(
                "started",
                _wall_stamp(recalled.created_at, wall_now),
                "its first launch under pptmstr, " + _FROM_FILE,
            )
        )
    else:
        rows.append(_unknown("started", "its session file records no first launch"))
    if recalled.last_active is not None:
        rows.append(_stated("last active", _wall_stamp(recalled.last_active, wall_now), _FROM_FILE))
    else:
        rows.append(_unknown("last active", "its session file records no last activity"))
    if recalled.ended_how is not None:
        rows.append(_stated("ended", ended_text(recalled), _FROM_FILE))
    else:
        rows.append(
            _unknown(
                "ended",
                "its session file records no end to its last run: the app was killed or "
                "crashed under it, or the file does not say",
            )
        )
    return rows


def _time_rows(
    rec: AgentRecord, now: float, wall_now: float, last_active: float | None = None
) -> list[Row]:
    """
    ``last_active`` is the wall-clock instant the caller holds for the node's
    last activity, or None when it holds none.
    """
    if rec.recalled is not None:
        return _recalled_time_rows(rec.recalled, wall_now)
    rows: list[Row] = []
    if rec.started_at > 0.0:
        rows.append(_stated("started", _stamp(rec.started_at, now, wall_now)))
    else:
        rows.append(_unknown("started", "its record carries no start time"))
    if rec.ended_at is not None:
        rows.append(_stated("ended", _stamp(rec.ended_at, now, wall_now)))
    elif rec.state_since > 0.0:
        rows.append(_stated("in this state since", _stamp(rec.state_since, now, wall_now)))
    if last_active is not None:
        rows.append(_stated("last active", _wall_stamp(last_active, wall_now)))
    else:
        rows.append(
            _unknown(
                "last active",
                "the store keeps when a state began, not the last thing an agent did",
            )
        )
    return rows


def _spend_rows(rec: AgentRecord) -> list[Row]:
    if rec.recalled is not None:
        cost = rec.recalled.cost_usd
        return [
            (
                _stated(
                    "cost",
                    f"${cost:,.2f}",
                    "an estimate, "
                    + _FROM_FILE
                    + "; it may double-count across resumes; nothing was spent in this window",
                )
                if cost is not None
                else _unknown("cost", "its session file records none")
            ),
            _unknown("tokens", _NOT_RUN),
        ]
    usage = rec.usage
    return [
        _stated("cost", f"${usage.total_cost_usd:,.2f}", "estimated by the SDK, not billing"),
        _stated(
            "tokens",
            f"{_tokens(usage.input_tokens)} in, {_tokens(usage.output_tokens)} out, "
            f"{_tokens(usage.cache_read_input_tokens)} read from cache, "
            f"{_tokens(usage.cache_creation_input_tokens)} written to it",
        ),
    ]


def _context_row(rec: AgentRecord) -> Row:
    if rec.recalled is not None:
        return _unknown("context", _NOT_RUN)
    ctx = rec.context
    if ctx is None:
        return _unknown("context", "no reading of its context window is held")
    headroom = ctx.tokens_until_compaction
    parts = [f"{_tokens(ctx.used_tokens)} of {_tokens(ctx.max_tokens)} resident"]
    if headroom is None:
        parts.append("no compaction threshold (autocompact off, or none reported)")
    else:
        parts.append(f"{_tokens(headroom)} before it compacts")
    if ctx.compactions:
        parts.append(f"compacted {ctx.compactions}x")
    return _stated("context", "; ".join(parts), "polled, so slightly behind")


def _git_row(looked: Looked | None) -> Row:
    if looked is None or not looked.git_read:
        return _unknown("git layout", "the directory was not read for it")
    layout = looked.git
    match layout:
        case None:
            return _stated(
                "git layout",
                "ordinary: under no git, or a checkout launched at its root",
            )
        case launcher.LinkedWorktree():
            main = layout.main_checkout
            where = "at its root" if layout.at_root else f"under its root {layout.root}"
            if main is None:
                return _known(
                    "git layout",
                    f"a linked worktree ({layout.kind.value}), {where}; shares {layout.shared}",
                    copy=layout.shared,
                    note="copies the shared git directory",
                )
            return _known(
                "git layout",
                f"a linked worktree ({layout.kind.value}) of {main}, {where}",
                copy=main,
                note="copies the main checkout",
            )
        case launcher.OutsideGit(git_dir=git_dir):
            return _known(
                "git layout",
                f"governed by a git directory outside it: {git_dir}",
                copy=git_dir,
                note="copies the git directory",
            )
        case launcher.UnreadGit(entry=entry):
            return _unknown("git layout", f"{entry} could not be read as a git directory")
        case _:
            assert_never(layout)


def _gate_rows(rec: AgentRecord, held: Held | None) -> list[Row]:
    """
    The gate as granted, and whether the session is contained.

    The record's policy is what the launch was granted, which is not always what
    was asked for. What the session answers with now is the session object's, and
    is said only while the pool is running it.
    """
    if rec.recalled is not None:
        return _recalled_gate_rows(rec)
    granted = rec.policy.value
    ended = rec.state.is_terminal
    if held is not None and held.running and not ended:
        now = held.policy.value
        gate = _known(
            "gate",
            now if now == granted else f"{now} (launched under {granted})",
            copy=now,
            note="what it is answering with now",
        )
    elif ended:
        gate = _known("gate", granted, note="as granted at launch; the session is not running")
    else:
        gate = _known(
            "gate",
            granted,
            note="as granted at launch; no running session is held to say what it is now",
        )
    if held is None:
        contained = _unknown("contained", "this window holds no session object for this card")
    elif held.contained:
        contained = _stated("contained", "yes", "sandbox settings were on its CLI's command line")
    else:
        contained = _stated("contained", "no", "its CLI was started with no sandbox settings")
    return [gate, contained]


def _recalled_gate_rows(rec: AgentRecord) -> list[Row]:
    """
    The gate and the containment of a recalled session: the session file's. The
    record's own ``policy`` is a field default there and is not read.
    """
    recalled = rec.recalled
    assert recalled is not None
    granted = rec.granted_policy
    if granted is None:
        gate = _unknown("gate", "its session file names no rung this build knows")
    else:
        gate = _known("gate", granted.value, note=_FROM_FILE + "; the session is not running")
    if recalled.contained is None:
        contained = _unknown("contained", "its session file does not say")
    else:
        contained = _stated(
            "contained", "yes" if recalled.contained else "no", "of its last run, " + _FROM_FILE
        )
    return [gate, contained]


def _transcript_row(rec: AgentRecord, held: Held | None, looked: Looked | None) -> Row:
    recalled = rec.recalled
    if recalled is not None and recalled.transcript is TranscriptPresence.GONE:
        where = (
            f"; the CLI's naming would put it at {looked.transcript}"
            if looked is not None and looked.transcript is not None
            else ""
        )
        return _stated("transcript", "gone", f"the CLI no longer lists this session{where}")
    if held is not None and held.transcript:
        return _known("transcript", held.transcript, note="as its CLI reported it")
    if looked is not None and looked.transcript is not None:
        there = "" if looked.transcript_there else "; no file is there"
        return _known(
            "transcript",
            looked.transcript,
            note=f"where the CLI's naming puts it; the session has not reported one{there}",
        )
    return _unknown("transcript", "the session reported none and its directory is not known")


def _session_file_rows(looked: Looked | None) -> list[Row]:
    """
    Where pptmstr keeps what it knows about the session: its directory and the
    file in it.
    """
    labels = ("session directory", "session file")
    if looked is None or not looked.files_read:
        return [_unknown(label, "the disk was not read for it") for label in labels]
    if looked.session_dir is None or looked.session_file is None:
        return [_unknown(label, "its id is not one directory name") for label in labels]
    return [
        _known("session directory", looked.session_dir),
        _known(
            "session file",
            looked.session_file,
            note="" if looked.session_file_there else "no file is there",
        ),
    ]


def _board_rows(
    session_id: str, logging: board_pane.Logging | None, looked: Looked | None
) -> list[Row]:
    """
    Where the board log is and whether it is being written: the state the board
    pane's own line is drawn from, through the function that words it.

    The directory is the one this window opened for the session, and otherwise
    where a log for it goes (``sessionfile.board_dir``, read at the click). A
    legacy directory that still holds records of it is a row of its own.
    """
    entry = None if logging is None else logging.sessions.get(session_id)
    if entry is not None and entry.directory:
        directory = _known("board log", entry.directory)
    elif looked is not None and looked.board_dir is not None:
        directory = _known(
            "board log",
            looked.board_dir,
            note="where its records go; this window has opened no log for it",
        )
    elif logging is None:
        directory = _unknown("board log", "this window was told nothing about board logs")
    else:
        directory = _unknown("board log", "no log directory was opened for this session")
    rows = [directory]
    if looked is not None and looked.board_legacy is not None:
        rows.append(
            _known(
                "legacy board log",
                looked.board_legacy,
                note=(
                    "a second history of its board; nothing merges the two"
                    if looked.board_split
                    else "its records are here and not in its session directory; "
                    "read, never appended to"
                ),
            )
        )
    if logging is None:
        rows.append(_unknown("board recording", "this window was told nothing about board logs"))
        return rows
    line = board_pane.recording_line(logging.off, entry, logging.unread)
    if line is None:
        rows.append(_unknown("board recording", "a restore of its log is still being applied"))
    else:
        rows.append(_stated("board recording", line.text))
    return rows


def view(
    snap: Snapshot,
    node: NodeId,
    *,
    held: Held | None,
    looked: Looked | None,
    logging: board_pane.Logging | None,
    now: float,
    wall_now: float,
    last_active: float | None = None,
) -> View | None:
    """
    Everything Details shows for ``node``, or None when the snapshot has no such
    node. Pure: ``now`` is the frame's monotonic clock and ``wall_now`` the wall
    clock read beside it.

    ``last_active`` is the wall-clock instant of the node's last activity, when
    the caller holds one; without it the row says it is not known. Not read for
    a recalled session, whose row is its session file's.
    """
    rec = snap.get(node)
    if rec is None:
        return None
    if node[1] is not None:
        return _agent_view(
            snap, rec, looked=looked, now=now, wall_now=wall_now, last_active=last_active
        )
    return _session_view(
        snap,
        rec,
        held=held,
        looked=looked,
        logging=logging,
        now=now,
        wall_now=wall_now,
        last_active=last_active,
    )


def _directory_row(rec: AgentRecord) -> Row:
    if rec.cwd:
        return _known("directory", rec.cwd)
    return _unknown("directory", "its record carries none")


def _session_view(
    snap: Snapshot,
    rec: AgentRecord,
    *,
    held: Held | None,
    looked: Looked | None,
    logging: board_pane.Logging | None,
    now: float,
    wall_now: float,
    last_active: float | None = None,
) -> View:
    session_id = rec.node_id[0]
    recalled = rec.recalled
    identity = [
        _known("session id", session_id),
        _title_row(rec),
        _state_row(rec),
    ]
    # A recalled session's failure is its file's: the record is DONE and holds
    # no error of a run this process saw.
    failed = (
        rec.state is AgentState.FAILED
        if recalled is None
        else recalled.ended_how is EndedHow.FAILED or failed_then_closed(recalled)
    )
    error = rec.error if recalled is None else recalled.error
    if failed:
        if error:
            identity.append(_known("error", flat_line(error), copy=error))
        else:
            identity.append(_unknown("error", "no detail was recorded"))
    identity.append(
        _known("team template", one_line(rec.template))
        if rec.template
        else _unknown("team template", "its record carries none")
    )
    identity.append(_known("model", one_line(rec.model)))
    if recalled is not None:
        identity.append(
            _unknown("sub-agents", "not loaded: the session has not run in this window")
        )
    else:
        subs = snap.subagents_of(rec.node_id)
        ended = sum(1 for sub in subs if sub.state.is_terminal)
        identity.append(_stated("sub-agents", f"{len(subs)}, {ended} ended" if subs else "none"))

    where = [_directory_row(rec), _git_row(looked)]
    if rec.session_base:
        where.append(_known("writes measured from", rec.session_base))
    else:
        where.append(_unknown("writes measured from", "its record carries no base"))

    files = [_transcript_row(rec, held, looked)]
    if rec.brief:
        files.append(_known("brief", rec.brief))
    elif recalled is not None:
        files.append(_unknown("brief", "its session file names no brief directory"))
    else:
        files.append(_stated("brief", "none", "launched with only its task"))
    files += _session_file_rows(looked)
    files += _board_rows(session_id, logging, looked)

    return View(
        title=flat_line(rec.task),
        sections=(
            Section("session", tuple(identity)),
            Section("where it runs", tuple(where)),
            Section("gate", tuple(_gate_rows(rec, held))),
            Section(
                "time and cost",
                (
                    *_time_rows(rec, now, wall_now, last_active),
                    *_spend_rows(rec),
                    _context_row(rec),
                ),
            ),
            Section("files", tuple(files)),
        ),
    )


def _agent_view(
    snap: Snapshot,
    rec: AgentRecord,
    *,
    looked: Looked | None,
    now: float,
    wall_now: float,
    last_active: float | None = None,
) -> View:
    session_id, agent_id = rec.node_id
    assert agent_id is not None
    root = snap.get((session_id, None))
    identity = [
        _known("address", board_mod.role_name(snap, rec.node_id, session_id)),
        _known("agent id", agent_id),
        (
            _known("type", one_line(rec.agent_type))
            if rec.agent_type
            else _unknown("type", "its record carries none")
        ),
        _state_row(rec),
        _known("model", one_line(rec.model)),
    ]
    belongs = [
        _known("session id", session_id),
        (
            _known("session task", flat_line(root.task), copy=root.task)
            if root is not None
            else _unknown("session task", "the session's own record is not in the store")
        ),
        _directory_row(rec),
    ]
    if looked is not None and looked.agent_transcript is not None:
        if looked.agent_transcript_there:
            transcript = _known("transcript", looked.agent_transcript)
        else:
            transcript = _unknown(
                "transcript",
                f"no file at {looked.agent_transcript}, which is where the CLI's naming "
                "puts it; it may be nested deeper under that directory",
            )
    else:
        transcript = _unknown("transcript", "its directory is not known")
    return View(
        title=flat_line(rec.agent_type or rec.task),
        sections=(
            Section("agent", tuple(identity)),
            Section("the session it belongs to", tuple(belongs)),
            Section(
                "time and cost",
                (*_time_rows(rec, now, wall_now, last_active), *_spend_rows(rec)),
            ),
            Section("files", (transcript,)),
        ),
    )


# ---------------------------------------------------------------------------
# Dear ImGui
# ---------------------------------------------------------------------------


def draw(state: DetailsState, shown: View | None) -> None:
    """
    Draw the modal for ``state.node`` from ``shown``. At root level, where the
    launcher is drawn.

    Escape closes it, read here because keyboard navigation is off and Dear ImGui
    closes nothing on Escape without it. Kept inside the viewport by the
    launcher's rule (``launcher.modal_origin``): it is as wide as the launcher and
    can be taller.
    """
    if state.node is None:
        return
    if shown is None:
        state.close()
        return
    if state._opening:
        state._opening = False
        imgui.open_popup(TITLE)

    viewport = imgui.get_main_viewport()
    imgui.set_next_window_pos(viewport.get_center(), imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5))
    imgui.set_next_window_size_constraints(
        imgui.ImVec2(_WIDTH, 0.0), imgui.ImVec2(_WIDTH, viewport.work_size.y)
    )
    last = imgui.internal.find_window_by_name(TITLE)
    if last is not None and last.was_active and not last.appearing:
        moved = launcher.modal_origin(
            (last.pos.x, last.pos.y),
            (last.size.x, last.size.y),
            (viewport.work_pos.x, viewport.work_pos.y),
            (viewport.work_size.x, viewport.work_size.y),
        )
        if moved is not None:
            imgui.set_next_window_pos(imgui.ImVec2(*moved), imgui.Cond_.always)
    opened, _ = imgui.begin_popup_modal(TITLE, None, imgui.WindowFlags_.always_auto_resize)
    if not opened:
        state.close()
        return
    listening = state._frames > 0
    state._frames += 1

    imgui.push_text_wrap_pos(0.0)
    imgui.text_colored(P.text_strong.vec4, author_text(shown.title))
    imgui.pop_text_wrap_pos()
    for at, section in enumerate(shown.sections):
        imgui.spacing()
        imgui.separator()
        imgui.text_colored(P.text_dim.vec4, section.title)
        _rows(at, section.rows)

    imgui.spacing()
    imgui.separator()
    dismissed = bool(imgui.button("close"))
    imgui.same_line()
    imgui.text_disabled("Esc closes")
    if listening and imgui.is_key_pressed(imgui.Key.escape):
        dismissed = True
    if dismissed:
        state.close()
        imgui.close_current_popup()
    imgui.end_popup()


def _rows(section: int, rows: tuple[Row, ...]) -> None:
    """
    One section as a table: the label, the value wrapped in the room that is left,
    and the copy control in a column of its own so it does not move with the text.
    """
    flags = imgui.TableFlags_.sizing_fixed_fit | imgui.TableFlags_.no_saved_settings
    if not imgui.begin_table(f"##details-{section}", 3, flags):
        return
    imgui.table_setup_column("label", imgui.TableColumnFlags_.width_fixed, _LABEL_W)
    imgui.table_setup_column("value", imgui.TableColumnFlags_.width_stretch)
    imgui.table_setup_column("copy", imgui.TableColumnFlags_.width_fixed, _COPY_W)
    for at, row in enumerate(rows):
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.text_colored(P.text_dim.vec4, row.label)
        imgui.table_next_column()
        imgui.push_text_wrap_pos(0.0)
        if row.known:
            imgui.text_colored(P.text.vec4, author_text(row.value))
        else:
            imgui.text_colored(P.warn.vec4, row.value)
        if row.note:
            imgui.text_disabled(author_text(row.note))
        imgui.pop_text_wrap_pos()
        imgui.table_next_column()
        if row.copy is not None and imgui.small_button(f"copy##{section}-{at}"):
            imgui.set_clipboard_text(row.copy)
    imgui.end_table()
