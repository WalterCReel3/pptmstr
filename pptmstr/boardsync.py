"""
The shell around ``boardlog``: where the logs live, and the one thread that appends.

**Where files go.** A session's records are the files of
``sessionfile.board_dir(<session id>)`` -- ``sessions/<session id>/board/`` under
pptmstr's own configuration directory, not ``~/.claude`` -- one per record, as
``boardlog`` lays them out, beside the session's ``session.json``. ``find`` reads
there and ``BoardWriter`` appends there and nowhere else.

**The legacy path** is ``legacy_root() / <session id>``: ``boards/<session id>/``
under the same configuration directory, where every log was kept before a session
had a directory of its own. This build reads a log there and never appends to one.

**``migrate`` carries the legacy logs over, once, before any writer exists.** One
listing of the legacy root and, per entry, one rename; no record is read. For
each entry it answers with a value:

- a directory whose session has nothing at the new path is renamed there
  (``Moved``), and a relative symbolic link to it is left at the old name. The
  link is what keeps one history: ``boardlog.append`` makes the directory it is
  given, so a pptmstr that still appends by the old name -- an older build left
  running, or one started later -- would otherwise make a second directory and
  report every record written. Through the link its listing, its temp file and
  its link land in the one log, and its ``read`` reads that log. ``Moved.unlinked``
  is why there is no such link, when there is none;
- a link this left on an earlier start is ``Linked``, and nothing is done;
- anything else is ``Left`` where it is, with the reason: a rename that failed for
  any cause, a name that is not a session id, an entry that is not a directory, a
  link that leads somewhere else, and -- ``Left.twice`` -- a session that has an
  entry at both paths.

**A session with records in both places is two histories, and neither is taken
for the whole board.** A rename is atomic, so both exist only when something
appended under the old name after the move: between the rename and the link, or
by a build that predates the move resuming a session this build started. The old
directory then holds the later facts and the new one the earlier, and nothing
merges them, since a merge is the second history ``BoardWriter`` exists to refuse.
``find`` restores from the new path, names the other, and says the session runs
unrecorded; ``BoardWriter`` refuses its first record for the same reason whatever
its owner adopted. The same holds for a log that is only at the legacy path -- a
rename that failed, or ``migrate`` not run: it is restored from there and not
appended to, there or at the new path.

**Resolving either by hand**, with pptmstr closed. For a log left at the legacy
path, move the directory: ``mv boards/<id> sessions/<id>/board``. For two
histories, read both -- each record is one small JSON file, numbered in the order
it was written -- keep the directory that is the board, and move the other out of
both roots (anywhere else; pptmstr reads only these two). If the one kept is under
``boards/``, move it to ``sessions/<id>/board`` afterwards. Nothing here does
either, because choosing is discarding and that is the operator's to do.

**Nothing here, or anywhere in pptmstr, deletes a log.** A session's directory grows
by one small file per board fact for as long as the session is resumed, and it stays
after the session is gone. It outlives the CLI's own transcript of that session,
which the CLI removes on a retention schedule this module does not follow.
Specifications, results and concern bodies are in those files as plain text.

**An append never runs on the thread that asked for it.** One costs a durable flush
and a listing of the session's directory -- milliseconds, growing with the length of
the log -- and the thread that asks is the one that draws. ``BoardWriter.submit``
queues the record and returns; a single worker thread takes records off the queue
one at a time, which is the whole of the ordering guarantee: **records reach disk in
the order they were submitted**, across every session the writer serves, because
nothing else in the process appends.

**The limit of that guarantee is the process.** Two pptmstr processes writing one
session id are two queues. ``boardlog.append`` still loses neither's records, and
``boardlog.restore`` reads the result as the ``contested`` case its docstring
describes. Two ``BoardWriter`` objects in one process are the same two queues, so a
process holds one.

**Two windows.** Both read and append under ``sessions/``. The second to start
finds the first's moves done: each old name is a link (``Linked``) and nothing
moves twice. A window that was appending to a session's log by its old name while
another moved it keeps appending to the same log once the link is there. An append
of its own caught between the rename and the link either fails, which ends that
window's recording of that session with the reason, or remakes the old directory,
which is the two histories above: ``migrate`` reports it as a ``Moved`` with
``unlinked`` set, and every ``find`` after reports the session unrecorded.

**A record that did not land is a value the owner is handed.** Every submission
produces exactly one ``Outcome``, collected for ``BoardWriter.drain``: on disk, on
disk behind a break, refused, failed, or not written before ``close`` gave up
waiting. ``drain`` takes a lock held only around list operations, never around IO,
so it is safe to call every frame.

**The first record that does not land ends the recording of its session.** Records
are facts about each other, and ``boardlog`` numbers them by what is on disk. A
completion that failed to write, followed by records that succeed, is a log with
contiguous ordinals that reads back whole, with no damage, as a board on which the
task was never finished. So once one record for a session is ``Unwritten`` --
whatever the cause, a full disk for one moment included -- nothing further is
appended for that session by this writer: the log on disk stays a true prefix of
the board, which is what ``boardlog.read`` promises of it. The break is reported
once as a ``Stopped``, and each later submission as an ``Outcome`` that names it.
``Written`` with a ``gap`` is not this case: the break is already on disk, a reader
already stops at it, and appending behind it hides nothing.

**A prefix has to say that it is one.** A log that ends at its last good record
reads back whole: the task whose completion did not land is held, what was declared
afterwards never existed, and nothing on disk disagrees. So at the stop the worker
appends one ``boardlog.RecordingStopped`` with the reason, and a board restored
from that log is ``short`` and says why. It is attempted once, only for a log this
writer was admitted to, and ``Stopped.marker`` is what became of it.

**What that leaves undetectable**, because the marker is an append like any other:
a stop caused by something that also refuses the marker (a full disk, a directory
that is gone, a mount that hangs); records ``close`` gave up on at its bound, whose
worker is by then held in an append and cannot be asked for another; and a process
killed with records still queued. Each leaves a log that is a true prefix and
restores as a whole board. Closing those needs a record of a clean end rather
than of an unclean one, which the format does not have.

**A record too long to log is such a stop, and no call an agent makes can cause
one.** The bus bounds every text where it accepts it, sized so that the largest
record a call can produce is about two thirds of ``boardlog.MAX_RECORD_BYTES``
in the characters that take the most bytes (``bus.MAX_TEXT_CHARS`` and its
neighbours, where the arithmetic is), and an argument the operator rewrote in
review reaches the same handler and the same bound. What is not bounded is what
the operator types into the board pane itself: a specification amended there is
stored at whatever length it was typed.

**A log this process did not start is appended to only once its owner says it holds
that board.** A record appended behind a history nobody restored comes from a
reducer that never saw that history: it grants what the logged board would refuse,
and ``restore`` then finds a second declaration or a second claim and marks the
task contested. So the first record for a session is written only when
``BoardWriter.adopt`` was called for it, or when the session's directory holds no
record at the moment the worker reaches that record. ``load`` admits nothing,
because reading a board is not having applied it: ``adopt`` is the statement that
the store now holds what the log says. The check and the append that follows it are
both on the worker thread, so nothing in this process can come between them;
another process can, and that is the limit above. A directory that could not be
listed is not one that holds no record: the session is refused, since what would be
written behind is unknown.

The worker is a daemon thread and not an executor. An executor's worker is joined
when the interpreter exits, with no bound, so an append held by a dead network mount
would hold the exit. A daemon thread ended mid-append tears nothing: a record's name
is linked only once its bytes are complete.
"""

from __future__ import annotations

import os
import queue
import stat
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from . import boardlog, sessionfile, settings
from .boardlog import Board, Record, Unwritten, Written

# The most of a failure's reason a ``RecordingStopped`` carries. A reason is an
# exception's text and has no bound of its own, and the marker has to land in the
# case where the record it stands for was too long to.
MARKER_REASON_CHARS = 400

# How long ``close`` waits for queued records unless told otherwise. An append is
# milliseconds, so this is a bound on a wedged filesystem and not a budget.
CLOSE_TIMEOUT = 5.0


def legacy_root() -> Path:
    """
    The directory every session's board log was kept under before a session had a
    directory of its own. Read from and moved out of, never appended under. Does
    not create it.
    """
    return settings.config_dir() / "boards"


def _places(root: Path | None, legacy: Path | None) -> tuple[Path, Path | None]:
    """
    The sessions root and the legacy root a call works against.

    With neither given they are the two under the configuration directory. A caller
    that names ``root`` has chosen where its logs are, and gets a legacy root only
    by naming that too: the one under the configuration directory has nothing to do
    with a root somewhere else.
    """
    if root is None:
        return sessionfile.root(), legacy_root() if legacy is None else legacy
    return root, legacy


@dataclass(frozen=True, slots=True)
class NoLog:
    """
    There is no board to restore for a session, and why: its id names no directory,
    or the directory holds nothing ``boardlog.read`` could report.

    Not a ``Board`` with nothing on it, so that "nothing was logged" and "a log was
    restored and it is empty-handed because it is damaged" cannot be confused.
    """

    reason: str


_NOT_A_NAME = "the session id is not one directory name"


@dataclass(frozen=True, slots=True)
class Found:
    """
    What a session's log restores as, where it was read, and whether a writer may
    append for the session.

    ``directory`` is the one ``board`` was read from: the session's own, or its
    legacy directory when that is the only place its records are. None for an id
    that names no directory.

    ``second`` is a directory that holds records of this session which are not in
    ``board``, when there are two histories. It is named and not read.

    ``unrecorded`` **is the answer to "may this be adopted".** None when the
    session's own directory holds everything this process can find for it. A
    reason, in words for the operator, when it does not: ``BoardWriter`` refuses
    the session's first record on the same test, so adopting changes nothing, and
    this is where the shell learns why before that refusal arrives.
    """

    board: Board | NoLog
    directory: Path | None
    second: Path | None = None
    unrecorded: str | None = None


def _elsewhere(session_id: str, directory: Path, legacy: Path | None) -> Path | None:
    """
    The session's legacy directory, when records of it are there, or may be, that
    are not under ``directory``. None when there is no such place.

    A legacy name that is ``directory`` itself -- the link ``migrate`` leaves -- is
    not a second place. One that could not be listed is: "nobody could look" is not
    "no record is here", as for the session's own directory.
    """
    if legacy is None:
        return None
    old = boardlog.session_dir(legacy, session_id)
    if old is None:
        return None
    try:
        if os.path.samefile(old, directory):
            return None
    except OSError:
        # One of the two is not there, which next_ordinal answers for the old one.
        pass
    return None if boardlog.next_ordinal(old) == 0 else old


def _refusal(directory: Path, old: Path, held: bool) -> str:
    """
    Why nothing is appended for a session with records under the legacy root.
    ``held`` is whether its own directory holds any as well.
    """
    if held:
        return (
            f"it has records in two places, {directory} and {old}; neither is the"
            " whole board and nothing merges them, so it runs unrecorded until one"
            " history is left"
        )
    return (
        f"its log is at the legacy path {old} and not at {directory}; a log there is"
        " read and never appended to, so it runs unrecorded until that directory is"
        " moved"
    )


def _board(log: boardlog.Log) -> Board | NoLog:
    if not log.records and log.damage is None:
        return NoLog("nothing is logged for this session")
    return boardlog.restore_log(log)


def find(session_id: str, root: Path | None = None, legacy: Path | None = None) -> Found:
    """
    The board ``session_id``'s log describes, read on the calling thread, which
    must therefore not be the one that draws. ``root`` and ``legacy`` are as
    ``_places`` has them.

    Read from the session's own directory. When that holds nothing and its legacy
    directory holds records, read from there, and ``unrecorded`` says so. When both
    hold records, read from its own, with the legacy one as ``second``.

    Creates nothing and admits nothing: a writer appends to this log only after
    ``BoardWriter.adopt``, and never when ``unrecorded`` is set. A log that is
    damaged from its first record is a ``Board`` whose ``short`` is true, not a
    ``NoLog``.

    Read as the disk holds it. Records a writer in this process has queued and
    not yet appended are not in the answer.
    """
    sessions, old_root = _places(root, legacy)
    directory = sessionfile.board_dir(session_id, sessions)
    if directory is None:
        return Found(NoLog(_NOT_A_NAME), None, unrecorded=_NOT_A_NAME)
    log = boardlog.read(directory)
    held = bool(log.records) or log.damage is not None
    old = _elsewhere(session_id, directory, old_root)
    if old is None:
        return Found(_board(log), directory)
    if held:
        return Found(_board(log), directory, old, _refusal(directory, old, True))
    return Found(_board(boardlog.read(old)), old, unrecorded=_refusal(directory, old, False))


def load(session_id: str, root: Path | None = None, legacy: Path | None = None) -> Board | NoLog:
    """
    ``find``'s board alone, for a caller that draws it and appends nothing.

    One that goes on to ``BoardWriter.adopt`` asks ``find``: this drops where the
    board was read and whether it may be appended behind.
    """
    return find(session_id, root, legacy).board


# -- carrying the legacy logs over ------------------------------------------------


@dataclass(frozen=True, slots=True)
class Moved:
    """
    A session's log was renamed from ``source`` to ``target``.

    ``unlinked`` is None when a link at ``source`` now leads to ``target``.
    Otherwise it is why not, and the old name is not guarded: a ``FileExistsError``
    in it means the name was taken again between the rename and the link, by a
    process appending there, and the session has two histories from this start on.
    """

    session_id: str
    source: Path
    target: Path
    unlinked: str | None = None


@dataclass(frozen=True, slots=True)
class Linked:
    """
    The legacy entry is the link an earlier ``migrate`` left: the log is already at
    ``target`` and nothing was done.
    """

    session_id: str
    source: Path
    target: Path


@dataclass(frozen=True, slots=True)
class Left:
    """
    A legacy entry was left exactly as it was found, and why.

    ``twice`` is whether the session also has an entry at its own path, which is
    the case the operator resolves by hand. ``name`` is the entry's, which is a
    session id only when it is one.
    """

    name: str
    source: Path
    reason: str
    twice: bool = False


Carried = Moved | Linked | Left


@dataclass(frozen=True, slots=True)
class Migration:
    """
    What ``migrate`` did with each entry of the legacy root, in name order.

    ``unlisted`` is why the legacy root could not be listed, in which case
    ``entries`` is empty and nothing was moved. A legacy root that is not there is
    an empty migration and not that.
    """

    entries: tuple[Carried, ...] = ()
    unlisted: str | None = None

    @property
    def moved(self) -> tuple[Moved, ...]:
        return tuple(e for e in self.entries if isinstance(e, Moved))

    @property
    def left(self) -> tuple[Left, ...]:
        return tuple(e for e in self.entries if isinstance(e, Left))


def migrate(root: Path | None = None, legacy: Path | None = None) -> Migration:
    """
    Rename every legacy log to its session's own directory. For the shell to call
    once, at startup, before a ``BoardWriter`` exists or a session is resumed: a
    log read while this runs could be read from either name.

    Blocks for one listing and a handful of calls per entry, and reads no record.
    Never raises, deletes nothing, and never renames onto a directory that holds
    anything -- ``os.rename`` refuses that itself, so the test and the move are
    one call. The module docstring has what each entry becomes.
    """
    sessions, old_root = _places(root, legacy)
    if old_root is None:
        return Migration()
    try:
        names = sorted(os.listdir(old_root))
    except FileNotFoundError:
        return Migration()
    except OSError as exc:
        return Migration(unlisted=f"{type(exc).__name__}: {exc}")
    return Migration(tuple(_carry(name, sessions, old_root) for name in names))


def _carry(name: str, sessions: Path, old_root: Path) -> Carried:
    source = old_root / name
    target = sessionfile.board_dir(name, sessions)
    if target is None:
        return Left(name, source, "its name is not a session id")
    try:
        mode = os.lstat(source).st_mode
        if stat.S_ISLNK(mode):
            if os.path.exists(target) and os.path.samefile(source, target):
                return Linked(name, source, target)
            return Left(
                name,
                source,
                f"it is a symbolic link that does not lead to {target}; a link is" " not moved",
                twice=os.path.lexists(target),
            )
        if not stat.S_ISDIR(mode):
            return Left(name, source, "it is not a directory", os.path.lexists(target))
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Left(name, source, f"{type(exc).__name__}: {exc}")
    try:
        os.rename(source, target)
    except OSError as exc:
        twice = os.path.lexists(target)
        return Left(
            name,
            source,
            (
                f"the session already has an entry at {target}"
                if twice
                else f"it could not be renamed to {target}"
            )
            + f" ({type(exc).__name__}: {exc})",
            twice,
        )
    return Moved(name, source, target, _link(source, target, old_root))


def _link(source: Path, target: Path, old_root: Path) -> str | None:
    """
    Leave a link at ``source`` that leads to ``target``, and say why not when it
    could not be left. Relative, so the configuration directory can be moved whole;
    from the real paths, so it is right when the legacy root is itself a link.
    """
    try:
        os.symlink(os.path.relpath(os.path.realpath(target), os.path.realpath(old_root)), source)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


@dataclass(frozen=True, slots=True)
class Outcome:
    """
    What became of one submitted record.

    ``result`` is ``boardlog.append``'s own answer where the append ran. Where it
    did not -- the session id names no directory, the session has records under
    the legacy root, the log was not adopted, the session's recording had stopped,
    the writer was closed, the append raised -- it is an ``Unwritten`` saying which.
    """

    session_id: str
    record: Record
    result: Written | Unwritten

    @property
    def kept(self) -> bool:
        """
        Whether a restore will read this record back, as far as the append could
        tell: it is on disk and its own listing showed no break below it.

        False is what the operator is owed a line for. ``Written`` with a ``gap``
        is False: the bytes are there and ``boardlog.read`` stops before them.
        """
        return isinstance(self.result, Written) and self.result.gap is None


@dataclass(frozen=True, slots=True)
class Stopped:
    """
    A session's board is no longer being recorded by this writer, from ``record``
    on, and why.

    Reported once per session, directly after the ``Outcome`` of the record that
    did not land. Nothing ends it within the process.

    ``marker`` is whether the log on disk says so, which is whether the next
    restore of it will. ``Written`` with no ``gap``: it reads back short, with the
    reason. ``Unwritten``: the log ends at its last good record and restores as
    though it were the whole board. None: no marker was attempted, because this
    writer was never admitted to that log -- the id names no directory, the session
    has records under the legacy root, the log is one this process did not adopt,
    or its directory could not be listed -- and what is on disk there is somebody
    else's board, as whole as they left it.
    """

    session_id: str
    record: Record
    reason: str
    marker: Written | Unwritten | None = None


Report = Outcome | Stopped


class BoardWriter:
    """
    The one appender. ``submit`` from any thread, ``drain`` on the owner's schedule,
    ``close`` once at shutdown.

    ``root`` is the sessions root and ``legacy`` the legacy one, as ``_places`` has
    them, asked once, when this is built. Records go under ``root`` only. ``legacy``
    is looked at and never written: a session with records there is refused at its
    first record, adopted or not, since its own directory is then not its whole
    history.
    """

    def __init__(self, root: Path | None = None, legacy: Path | None = None) -> None:
        self.root, self.legacy = _places(root, legacy)
        self._changed = threading.Condition(threading.Lock())
        # Read and written only with ``_changed`` held, down to ``_abandoned``.
        self._queue: queue.SimpleQueue[tuple[str, Record] | None] = queue.SimpleQueue()
        self._pending: deque[tuple[str, Record]] = deque()
        self._reports: list[Report] = []
        self._admitted: set[str] = set()
        self._worker: threading.Thread | None = None
        # Whether the worker is inside an append for the oldest pending record.
        self._in_flight = False
        self._closed = False
        self._abandoned = False
        # The worker's alone. ``_whole`` is the sessions found to have no record
        # under the legacy root, and ``_split`` the ones found to have some.
        self._stopped: dict[str, Stopped] = {}
        self._whole: set[str] = set()
        self._split: set[str] = set()

    # -- the owner's side ----------------------------------------------------------

    def submit(self, session_id: str, record: Record) -> None:
        """
        Queue one record for ``session_id`` and return. Performs no IO.

        After ``close`` the record is not queued, and its outcome says so.
        """
        with self._changed:
            if self._closed:
                self._reports.append(
                    Outcome(session_id, record, Unwritten("submitted after the writer was closed"))
                )
                return
            self._pending.append((session_id, record))
            self._queue.put((session_id, record))
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name="boardsync", daemon=True)
                self._worker.start()

    def directory(self, session_id: str) -> Path | None:
        """
        Where ``session_id``'s records are appended, or None for an id that names
        no directory. Performs no IO.
        """
        return sessionfile.board_dir(session_id, self.root)

    def adopt(self, session_id: str) -> None:
        """
        Allow appends to a log this process did not start. Performs no IO.

        The caller's statement that its store holds the board ``load`` returned for
        ``session_id``, so it is called after that board is applied and before the
        session's first record is submitted. A record the worker reached earlier
        was refused, and that refusal stopped the session's recording: adopting
        afterwards does not restart it, since the log would then be missing a fact
        in its middle.

        It does not admit a session ``find`` reported as ``unrecorded``: the worker
        looks under the legacy root itself.
        """
        with self._changed:
            self._admitted.add(session_id)

    def drain(self) -> tuple[Report, ...]:
        """
        Everything settled since the last call, oldest first. Never waits on an
        append: empty when none has finished.
        """
        with self._changed:
            settled = tuple(self._reports)
            self._reports.clear()
        return settled

    @property
    def outstanding(self) -> int:
        """How many submitted records have no outcome yet."""
        with self._changed:
            return len(self._pending)

    def close(self, timeout: float = CLOSE_TIMEOUT) -> tuple[Report, ...]:
        """
        Stop taking records, wait at most ``timeout`` seconds for the queued ones,
        and return everything not yet drained.

        A record still queued when the wait ends is given up: it is returned as
        ``Unwritten`` and the worker writes nothing further. The one record whose
        append had already begun is returned the same way, and it alone may still
        reach disk afterwards -- its reason says so, since claiming either would be
        a guess.

        A second call waits for nothing and returns what has been reported since,
        which is nothing unless ``submit`` was called in between.
        """
        with self._changed:
            self._closed = True
            if not self._abandoned:
                self._changed.wait_for(lambda: not self._pending, timeout=max(timeout, 0.0))
                self._abandoned = True
                for position, (session_id, record) in enumerate(self._pending):
                    reason = (
                        "its append had begun and had not returned when the writer closed;"
                        " it may still reach disk"
                        if position == 0 and self._in_flight
                        else "still queued when the writer closed; not written"
                    )
                    self._reports.append(Outcome(session_id, record, Unwritten(reason)))
                self._pending.clear()
                self._queue.put(None)
            settled = tuple(self._reports)
            self._reports.clear()
        return settled

    # -- the worker's side ---------------------------------------------------------

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            with self._changed:
                if self._abandoned:
                    return
                self._in_flight = True
            session_id, record = item
            reports = self._settle(session_id, record)
            with self._changed:
                if self._abandoned:
                    # ``close`` has already reported this record. The next item
                    # off the queue ends the loop above.
                    continue
                self._in_flight = False
                self._pending.popleft()
                self._reports.extend(reports)
                self._changed.notify_all()

    def _settle(self, session_id: str, record: Record) -> tuple[Report, ...]:
        earlier = self._stopped.get(session_id)
        if earlier is not None:
            refusal = Unwritten(
                "not appended: this session's board stopped being recorded at an"
                f" earlier record ({earlier.reason})"
            )
            return (Outcome(session_id, record, refusal),)
        result: Written | Unwritten
        try:
            result = self._append(session_id, record)
        # Whatever an append raises is this record's outcome. The worker ending
        # on it would turn every later submission into a silent drop.
        except Exception as exc:
            result = Unwritten(f"{type(exc).__name__}: {exc}")
        outcome = Outcome(session_id, record, result)
        if isinstance(result, Written):
            return (outcome,)
        stopped = Stopped(session_id, record, result.reason, self._mark(session_id, result.reason))
        self._stopped[session_id] = stopped
        return (outcome, stopped)

    def _mark(self, session_id: str, reason: str) -> Written | Unwritten | None:
        """
        Append the one record that says ``session_id``'s log ends early, when the
        log is this writer's to append to. Tried once, and what became of the try
        is the answer.
        """
        directory = self.directory(session_id)
        with self._changed:
            admitted = session_id in self._admitted
        if directory is None or not admitted or session_id in self._split:
            return None
        marker = boardlog.RecordingStopped(at=time.time(), reason=reason[:MARKER_REASON_CHARS])
        try:
            return boardlog.append(directory, marker)
        except Exception as exc:
            return Unwritten(f"{type(exc).__name__}: {exc}")

    def _append(self, session_id: str, record: Record) -> Written | Unwritten:
        directory = self.directory(session_id)
        if directory is None:
            return Unwritten(_NOT_A_NAME)
        if session_id not in self._whole:
            # Once, at the session's first record: what appears there afterwards
            # is another process's doing, and the next ``find`` reports it.
            old = _elsewhere(session_id, directory, self.legacy)
            if old is not None:
                self._split.add(session_id)
                held = boardlog.next_ordinal(directory) != 0
                return Unwritten(f"not appended: {_refusal(directory, old, held)}")
            self._whole.add(session_id)
        with self._changed:
            admitted = session_id in self._admitted
        if not admitted:
            # No record in the listing is "this process is starting the log": a
            # directory that is missing, empty, or holds only another append's
            # temp file has no history to write behind.
            found = boardlog.next_ordinal(directory)
            with self._changed:
                # ``adopt`` may have been called while the listing ran.
                admitted = found == 0 or session_id in self._admitted
                if admitted:
                    self._admitted.add(session_id)
        if not admitted:
            if isinstance(found, boardlog.Unlisted):
                return Unwritten(
                    "this session's board log could not be listed, so whether it holds"
                    f" records this process did not adopt is not known ({found.reason})"
                )
            return Unwritten(
                "this session has a board log this process did not adopt;"
                " appending would put a second history behind it"
            )
        return boardlog.append(directory, record)
