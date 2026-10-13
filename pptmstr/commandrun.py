"""
Running a break-glass command: the operator's login shell, given exactly the
string that was approved, bounded, killable, and never waited on by a frame.

**What runs.** ``[run.shell, "-l", "-c", run.text]``, as an argument list. There
is no ``shell=True`` and no quoting layer, so the argument after ``-c`` is
``run.text`` as the operator approved it, encoded as the file system encoding
(UTF-8 here). Standard input is ``/dev/null``; standard output and error are one
pipe; the shell leads a new session, and so a new process group; the directory is
``run.cwd``; the environment is ``environment()`` of pptmstr's own, for that
directory.

**The order of things, for one command.** ``Runner.start`` reserves the
session's one slot and returns; everything else is on two threads of the
command's own. The *worker* waits, keeps the clock and kills. The *reader* moves
the pipe into the log. They are two so that a log that cannot be written -- the
one thing here that can block without a bound -- holds up neither the timeout
nor a stop.

1. ``commandfile.write_run``. Nothing is started before this returns
   ``Written``, which is after the record is flushed to the device and linked
   under its name. An ``Unwritten`` run ends as ``NotStarted`` and starts nothing.
2. ``commandfile.open_log``, then ``Popen``. Either failing is ``NotStarted``.
   Whether the command has been stopped or given up is asked before the log
   is made and again as the last thing before ``Popen``; asked for by then, no
   process is started. An ``abandon`` that arrives while ``Popen`` is at work
   is acted on the moment it returns: the process existed, and is killed.
3. ``commandfile.write_started``: the pid, which is the session's number and
   the group's, and what tells this process from a later one with its pid. A
   pptmstr that dies after this leaves a record the process can be found from
   (``survivor``). A record that could not be written is ``Ended.started``.
4. Output is read as it comes. The first ``max_log_bytes`` go to ``output.log``;
   the rest is counted and dropped, and the last ``tail_bytes`` of everything
   are kept in memory and written to ``output.tail`` when the log was cut.
5. The shell exits, or ``Run.timeout_s`` passes, or ``stop`` or ``close`` asks.
6. **The session is ended, whichever of those it was.** Every process group
   that has a live process of the shell's session is sent ``SIGTERM`` and
   given ``grace_s``, all of it, to go; then ``SIGKILL``, again at every look
   until the session is empty. The grace is cut short only by the whole
   session being gone, never by the shell being gone: a shell with no trap
   dies of ``SIGTERM`` at once, and what it started -- the ``make`` of
   ``cd x && make`` -- is what needed the time. After a shell that exited by
   itself this is what makes "ended" mean that nothing the command started
   in its session is left; how many processes it took is ``End.leftover``.
7. The pipe is read for at most ``drain_s`` more and closed. A process that
   left the session and holds the pipe is not waited for.
8. ``commandfile.write_end``, and then the callback, **on the worker thread**.

**Exactly one end for each command handed over.** Every path out of the worker
goes through one place that writes the end and calls the callback, a failure of
the runner itself included: before a process exists that is ``NotStarted``, and
after, ``EndUnknown`` with the session killed. A command given up
(``abandon``) follows the same line: ``NotStarted`` when no process had been
started, and ``EndUnknown`` when one had. A command id is taken once; the
same id handed over again is ignored, not ended a second time. ``Ended.record``
says whether ``end.json`` was written, and an ``Unwritten`` there is to be shown:
the end happened and the disk does not say so.

**One running command per session.** A second for a session that has one is not
queued: its run is recorded, and it ends as ``NotStarted``. The reducer treats a
second approval as a no-op, so this is the shell being told plainly when that
did not hold.

**Which processes are a command's.** Those whose *session* is the one the shell
leads, found by reading ``/proc``. Not those of its process group: a job of a
shell with job control has a group of its own, and so has whatever coreutils
``timeout`` runs (it calls ``setpgid(0, 0)``), and both stay in the session
(measured: after ``killpg`` on the shell's group, ``timeout 60 sleep 50`` and
its ``sleep`` are alive, with the shell's session and another group). The
shell is not reaped until the last signal has been sent, so its pid, which is
the session's number and its own group's, cannot be given to another process
while this module still signals; that is also why "is it empty" is asked of
``/proc`` and not of ``killpg(pgid, 0)``, which answers for the unreaped shell
(measured: it succeeds on a group that is one zombie).

Where ``/proc`` cannot be read -- there is none, or the read failed -- the
session cannot be counted and only the shell's own group can be signalled:
every wind-down then takes the whole grace, ``End.leftover`` is None,
``End.unconfirmed`` says that nothing but the shell was seen to go
(``UNCOUNTED``), and ``survivor`` answers ``NotLooked``.

**Gone, and not known, are two answers.** ``survivor`` says ``Gone`` only for
what it saw: another boot, an empty session, the shell's pid in other hands.
A ``/proc`` that could not be read is ``NotLooked``, and processes in the
session with the shell gone are ``Unidentified``, which is not named as the
command and is never paired with a line that kills.

**What is not guaranteed.**

- A process that leaves the session survives all of it: a child that calls
  ``setsid``, or double-forks into its own session, is in no group this
  module signals and outlives the command's end, the timeout, a stop and
  pptmstr. "Nothing left" is always "nothing left in the shell's session". A
  command that is meant to leave something running has to say so that way,
  in the text the operator reads.
- A pptmstr that is killed outright leaves the command running with no timeout
  and writes no end. ``PR_SET_PDEATHSIG`` is not used to prevent it, for three
  reasons. It reaches the shell alone, and a shell killed by a signal leaves
  what it started running, now without the process the record names. It is
  tied to the thread that forked, not to pptmstr. And setting it takes code
  run between ``fork`` and ``exec`` (``preexec_fn``), which ``subprocess``
  documents as unsafe in a process with threads, as this one is. What there is
  instead: the session's number is on disk before anything else is done with
  the process, and ``survivor`` finds it at the next start.
- A command whose shell has died and whose other processes have not, found
  after pptmstr itself died, is ``Unidentified``: reported, and not named.
- ``close`` returns after its bound whether or not every session is dead. What
  it names as unconfirmed has no end yet; its worker is a daemon thread and
  records one only if the process outlives it.
- A session sent ``SIGKILL`` that is still there ``REAP_S`` later is ended
  anyway, with ``End.unconfirmed`` saying so, and named by ``close``.
- A reader that never comes back from a write to the log is left behind with
  the pipe and the log open. The end is recorded without it.
- A shell that is not POSIX is run all the same: the operator's login shell is
  the decision. ``Shell.posix`` is there so the row can say that the breakdown
  it shows is bash's reading and not that shell's.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import pwd
import select
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from . import commandfile
from .command import (
    MAX_TAIL_CHARS,
    MAX_TAIL_LINES,
    Cancelled,
    CancelledBy,
    Command,
    CommandId,
    End,
    EndUnknown,
    Exited,
    NotStarted,
    Outcome,
    Started,
    Survivor,
    TimedOut,
)

# Between ``SIGTERM`` and ``SIGKILL``.
KILL_GRACE_S = 2.0

# How long the pipe is read after the shell is gone.
DRAIN_S = 1.0

# How long a shell sent ``SIGKILL`` is waited for before it is ended unreaped.
REAP_S = 5.0

# How often a worker looks at its process, the clock and what it has been asked.
# It is also the most a stop waits before the signal is sent.
TICK_S = 0.05

CLOSE_TIMEOUT = 5.0

SESSION_BUSY = "another command of this session is running"
RUNNER_CLOSED = "pptmstr was shutting down"

_CHUNK = 1 << 16

# The shells whose reading of a command string is the one the row's breakdown
# shows, by the name of the program.
_POSIX_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})


# -- which shell ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Shell:
    """
    The program a command is given to. ``source`` is where it was named:
    ``"SHELL"`` or ``"passwd"``. ``posix`` is whether its name is one of the
    shells that read a command string as bash does.
    """

    path: str
    posix: bool
    source: str


@dataclass(frozen=True, slots=True)
class NoShell:
    """
    Nothing names a shell that can be run, and why. Run is then not offered.
    """

    reason: str


def passwd_shell() -> str | None:
    """
    The login shell in this user's password entry, or None when there is none.
    """
    try:
        return pwd.getpwuid(os.getuid()).pw_shell or None
    except KeyError:
        return None


def _unusable(path: str) -> str | None:
    """
    Why ``path`` is not a shell that can be run, or None.
    """
    if not os.path.isabs(path):
        return "is not an absolute path"
    if not os.path.isfile(path):
        return "is not a file"
    if not os.access(path, os.X_OK):
        return "is not executable"
    return None


def shell_for(
    environ: Mapping[str, str] | None = None,
    passwd_lookup: Callable[[], str | None] = passwd_shell,
) -> Shell | NoShell:
    """
    The operator's login shell: ``$SHELL`` when that is an absolute path to an
    executable file, else the password entry's on the same terms, else
    ``NoShell`` saying what each of the two was.

    A relative ``$SHELL`` is not looked up on ``PATH``: which program that is
    would depend on the directory and the environment at the moment of the run.
    """
    env = os.environ if environ is None else environ
    tried: list[str] = []
    for source, path in (("SHELL", env.get("SHELL")), ("passwd", passwd_lookup())):
        where = "$SHELL" if source == "SHELL" else "the password entry's shell"
        if not path:
            tried.append(f"{where} is not set")
            continue
        problem = _unusable(path)
        if problem is None:
            return Shell(path=path, posix=os.path.basename(path) in _POSIX_SHELLS, source=source)
        tried.append(f"{where} ({path}) {problem}")
    return NoShell("; ".join(tried))


# -- which environment ------------------------------------------------------------

# A non-interactive bash sources the file ``BASH_ENV`` names before the command,
# and a POSIX shell the one ``ENV`` names. Inherited from wherever pptmstr was
# started, either could name a file the contained session can write.
_SOURCED_BY_A_SHELL = ("BASH_ENV", "ENV")


def _venv_bin(environ: Mapping[str, str]) -> str | None:
    venv = environ.get("VIRTUAL_ENV")
    return os.path.normpath(os.path.join(venv, "bin")) if venv else None


def _is_venv_bin(entry: str, venv_bin: str | None) -> bool:
    return venv_bin is not None and os.path.normpath(entry) == venv_bin


def _is_relative(entry: str) -> bool:
    """
    Whether an entry of a search path is looked up from the current directory:
    an empty one, ``.``, and every other that is not absolute. The directory a
    command runs in is the session's, which the session's agents write.
    """
    return not os.path.isabs(entry)


# The variables that list directories a program is found in ahead of the ones
# the system would use: a command for the shell, a shared object for the
# loader, a module for the three interpreters. An entry of any of them that is
# a directory the session writes is a file of the session's run in place of the
# operator's. ``LD_PRELOAD``, ``ZDOTDIR`` and their like name one thing and not
# a search, are set by nobody but the operator, and are passed as they are.
_SEARCHED = ("PATH", "LD_LIBRARY_PATH", "PYTHONPATH", "NODE_PATH", "PERL5LIB")


def _is_inside(entry: str, base: str) -> bool:
    """
    Whether an absolute entry, with its links followed, is ``base`` or under
    it. ``base`` is the command's directory with its own links followed.
    """
    real = os.path.realpath(entry)
    return real == base or real.startswith(base.rstrip(os.sep) + os.sep)


def _sorted_out(
    environ: Mapping[str, str], cwd: str | None
) -> dict[str, tuple[list[str], list[str], list[str], list[str]]]:
    """
    Each search path that is set, as four lists of its entries in their order:
    kept, not absolute, inside ``cwd``, and pptmstr's virtualenv's ``bin``
    (``PATH`` only). **Reads the disk** when ``cwd`` is given: every absolute
    entry has its links followed.
    """
    venv_bin = _venv_bin(environ)
    base = None if cwd is None else os.path.realpath(cwd)
    found = {}
    for name in _SEARCHED:
        if name not in environ:
            continue
        kept: list[str] = []
        relative: list[str] = []
        inside: list[str] = []
        venv: list[str] = []
        for entry in environ[name].split(os.pathsep):
            if _is_relative(entry):
                relative.append(entry)
            elif name == "PATH" and _is_venv_bin(entry, venv_bin):
                venv.append(entry)
            elif base is not None and _is_inside(entry, base):
                inside.append(entry)
            else:
                kept.append(entry)
        found[name] = (kept, relative, inside, venv)
    return found


def environment(environ: Mapping[str, str] | None = None, cwd: str | None = None) -> dict[str, str]:
    """
    What a command runs in: pptmstr's environment without the virtualenv pptmstr
    itself was started from, and without what would let a file in the
    session's directory run before or instead of the command.

    ``VIRTUAL_ENV`` is dropped and every ``PATH`` entry that is that
    virtualenv's ``bin`` is taken out. A login shell does neither by itself, and
    with them ``python`` and ``pip`` in the operator's command would be
    pptmstr's. ``BASH_ENV`` and ``ENV`` are dropped.

    From each of ``_SEARCHED`` every entry is taken out that is not an absolute
    path, the empty one among them, and every one that is ``cwd`` or inside it
    once its links are followed: ``<cwd>/node_modules/.bin``, a ``bin`` a
    directory hook put there. One of them left with no entry is unset. The rest
    is passed as it is, each list in its order.

    ``PATH`` is never left unset or empty: it is then ``os.defpath``. A shell
    reads an empty ``PATH`` as the current directory, and bash given none at
    all searches a default of its own that ends in ``.`` (both measured, bash
    5.2.15: ``x`` runs ``./x`` under ``PATH=`` and under no ``PATH``).

    Pure when ``cwd`` is None, which is the part that does not depend on the
    session; with a ``cwd`` it **reads the disk** to follow links.

    This is the environment the shell *starts* with. A login shell then runs
    the operator's profile, in the session's directory, and what that reads --
    and puts back on ``PATH`` -- is not this function's to bound.
    """
    env = dict(os.environ if environ is None else environ)
    sorted_out = _sorted_out(env, cwd)
    env.pop("VIRTUAL_ENV", None)
    for name in _SOURCED_BY_A_SHELL:
        env.pop(name, None)
    for name, (kept, _relative, _inside, _venv) in sorted_out.items():
        if kept:
            env[name] = os.pathsep.join(kept)
        else:
            del env[name]
    env.setdefault("PATH", os.defpath)
    return env


def _entries(count: int) -> str:
    return "entry" if count == 1 else "entries"


def environment_changes(
    environ: Mapping[str, str] | None = None, cwd: str | None = None
) -> tuple[str, ...]:
    """
    What ``environment`` takes out of ``environ`` for a command run in ``cwd``,
    one line each, for the row. Empty when it changes nothing. With no ``cwd``
    it is what is taken out whatever the directory is.
    """
    env = os.environ if environ is None else environ
    sorted_out = _sorted_out(env, cwd)
    lines: list[str] = []
    if env.get("VIRTUAL_ENV"):
        lines.append(f"VIRTUAL_ENV ({env['VIRTUAL_ENV']}) is unset")
    elif "VIRTUAL_ENV" in env:
        lines.append("VIRTUAL_ENV (empty) is unset")
    if "PATH" in sorted_out and sorted_out["PATH"][3]:
        lines.append(f"{_venv_bin(env)} is taken off PATH")
    for name in _SOURCED_BY_A_SHELL:
        if name in env:
            lines.append(
                f"{name} ({env[name]}) is unset: a shell would run that file before the command"
            )
    for name, (kept, relative, inside, _venv) in sorted_out.items():
        reader = "a shell" if name == "PATH" else "a program"
        if relative:
            shown = ", ".join(repr(e) for e in dict.fromkeys(relative))
            lines.append(
                f"{len(relative)} {name} {_entries(len(relative))} not an absolute "
                f"path ({shown}) {'is' if len(relative) == 1 else 'are'} taken off: {reader} looks "
                "each up in the directory the command runs in"
            )
        if inside:
            shown = ", ".join(repr(e) for e in dict.fromkeys(inside))
            lines.append(
                f"{len(inside)} {name} {_entries(len(inside))} inside the directory the "
                f"command runs in ({shown}) {'is' if len(inside) == 1 else 'are'} taken off: "
                "this session writes the files there"
            )
        if not kept and name != "PATH":
            lines.append(f"{name} is unset: no entry of it is left")
    if "PATH" not in sorted_out or not sorted_out["PATH"][0]:
        why = "no entry of it is left" if "PATH" in env else "none was set"
        lines.append(
            f"PATH is set to {os.defpath}: {why}, and a shell with none searches the"
            " current directory"
        )
    return tuple(lines)


# A sentence for the row's standing lines, beside the one that says the command
# runs as the operator: nothing in this module can make it untrue.
PROFILE_WARNING = (
    "Your shell profile runs first, in this directory: a profile hook that reads files "
    "from the current directory -- direnv, mise, a dotenv plugin -- reads files this "
    "session wrote."
)


# -- the tail ---------------------------------------------------------------------


def cut_tail(data: bytes) -> str:
    """
    The end of an output as ``End.tail`` holds it: decoded with what is not
    UTF-8 replaced, then its last ``MAX_TAIL_LINES`` lines, then the last
    ``MAX_TAIL_CHARS`` characters of those.
    """
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    if len(lines) > MAX_TAIL_LINES:
        text = "".join(lines[-MAX_TAIL_LINES:])
    return text[-MAX_TAIL_CHARS:]


# -- which processes are alive ----------------------------------------------------

_PROC = "/proc"


@dataclass(frozen=True, slots=True)
class _Process:
    pid: int
    state: str
    group: int
    session: int
    start_ticks: int


def _process(pid: int) -> _Process | None:
    """
    One process as ``/proc/<pid>/stat`` gives it, or None when there is none.
    ``OSError`` when there may be one and it could not be read: a file that
    cannot be opened for want of a descriptor is not a process that has gone.
    """
    try:
        with open(f"{_PROC}/{pid}/stat", "rb") as handle:
            stat = handle.read()
    except (FileNotFoundError, ProcessLookupError):
        # The second is a read of a process that went between the two calls.
        return None
    try:
        # The name, in parentheses, may itself hold spaces and parentheses.
        fields = stat.rsplit(b")", 1)[1].split()
        return _Process(
            pid=pid,
            state=fields[0].decode("ascii", "replace"),
            group=int(fields[2]),
            session=int(fields[3]),
            start_ticks=int(fields[19]),
        )
    except (ValueError, IndexError) as exc:
        raise OSError(f"{_PROC}/{pid}/stat is not what a process's is") from exc


def _session(sid: int) -> list[_Process] | None:
    """
    Every process whose session is ``sid`` and that is not a zombie, or None
    where there is no ``/proc`` to ask. ``OSError`` when there is one and it
    could not be read through: the answer is then not known, and is not "none".
    """
    if not os.path.exists(f"{_PROC}/self/stat"):
        return None
    found = []
    for name in os.listdir(_PROC):
        if name.isdigit():
            process = _process(int(name))
            if process is not None and process.session == sid and process.state not in "ZX":
                found.append(process)
    return found


def session_members(sid: int) -> tuple[int, ...] | None:
    """
    The pids of the live processes in a session -- a zombie is not one -- or
    None where that cannot be told: no ``/proc``, or one that could not be
    read. Never raises. Reads ``/proc`` once through.
    """
    try:
        found = _session(sid)
    except OSError:
        return None
    return None if found is None else tuple(p.pid for p in found)


def _groups(processes: list[_Process]) -> tuple[int, ...]:
    """
    The process groups ``processes`` are in, ascending. A group of 0 is one
    this pid namespace cannot see, and 0 and 1 given to ``killpg`` are this
    process's own group and every process there is: neither is ever named.
    """
    return tuple(sorted({p.group for p in processes if p.group > 1}))


def _boot_id() -> str | None:
    try:
        with open(f"{_PROC}/sys/kernel/random/boot_id", encoding="ascii") as handle:
            return handle.read().strip() or None
    except (OSError, ValueError):
        return None


def started_now(pid: int, at: float) -> Started:
    """
    The record of a process this process has just started and not reaped, which
    leads its own session and group.
    """
    try:
        process = _process(pid)
    except OSError:
        process = None
    return Started(
        pid=pid,
        pgid=pid,
        at=at,
        start_ticks=None if process is None else process.start_ticks,
        boot_id=_boot_id(),
    )


@dataclass(frozen=True, slots=True)
class Gone:
    """
    A command's process was looked for and nothing of it is alive. ``why`` is
    what showed it, in pptmstr's words.

    This does not change: a command seen gone is gone at every later look, and
    the number it had is from then on some other process's.
    """

    why: str


@dataclass(frozen=True, slots=True)
class Unidentified:
    """
    ``members`` processes are alive in session ``session``, which is the number
    of the session a command's shell led, and the shell is not among them.

    They are what the command left running, or they are another program's: a
    session's number is its leader's pid, which is handed out again once the
    whole session has gone, and a later process given it that starts a session
    and exits leaves exactly this. ``started.json`` holds what tells the shell
    from a later process with its pid, and nothing that tells one leaderless
    session from another. So nothing here is named as the command's, and
    nothing built on this prints a line that kills it.
    """

    session: int
    members: int


@dataclass(frozen=True, slots=True)
class NotLooked:
    """
    Whether anything of a command is alive could not be told, and ``reason``
    is why: no ``/proc``, one that could not be read, or a record without the
    two facts that tell a process from a later one with its number. Not "gone".
    """

    reason: str


Looked = Survivor | Gone | Unidentified | NotLooked

NO_PROC = "there is no /proc here to look in"
NOT_ON_RECORD = (
    "its record does not hold when its shell started, which is what tells that process"
    " from a later one with its pid"
)
ANOTHER_BOOT = "the machine has been restarted since it was started"
NUMBER_REUSED = "its shell's pid now belongs to a process that started at another time"
NOTHING_ALIVE = "no process is alive in the session its shell led"


def survivor(started: Started) -> Looked:
    """
    What is alive, now, of the command whose shell ``started`` names.

    The shell led a session, and a process it started is in that session
    whatever group it has moved to, unless it left with ``setsid``. So the
    session is what is looked through, and ``Survivor.members`` counts it.

    - ``Survivor`` only when the shell itself is still there -- alive or an
      unreaped zombie -- under its pid with its start time, in the boot the
      record was written in. A session's number is not handed out again while
      its leader exists, so the processes listed *before* the shell was seen
      are that session's and nobody else's.
    - ``Gone`` when the record is of another boot, when the session has no
      live process, or when the shell's pid is now another process's: that
      pid was free to be given away, which it is not while the session lives.
    - ``Unidentified`` when processes are in the session and the shell is not.
    - ``NotLooked`` when none of this could be read.

    Never raises. Reads ``/proc`` once through.
    """
    if started.start_ticks is None or started.boot_id is None:
        return NotLooked(NOT_ON_RECORD)
    try:
        members = _session(started.pid)
        if members is None:
            return NotLooked(NO_PROC)
        boot_id = _boot_id()
        if boot_id is None:
            return NotLooked(f"{_PROC}/sys/kernel/random/boot_id could not be read")
        if boot_id != started.boot_id:
            return Gone(ANOTHER_BOOT)
        # After the listing, not before: see the docstring.
        leader = _process(started.pid)
    except OSError as exc:
        return NotLooked(f"/proc could not be read: {type(exc).__name__}: {exc}")
    if leader is not None and leader.start_ticks != started.start_ticks:
        return Gone(NUMBER_REUSED)
    if not members:
        return Gone(NOTHING_ALIVE)
    if leader is None:
        return Unidentified(session=started.pid, members=len(members))
    return Survivor(
        pid=started.pid, pgid=started.pgid, members=len(members), groups=_groups(members)
    )


def kill_line(found: Survivor) -> str:
    """
    The command that ends a survivor, for the operator to copy: a signal to
    every process group that was found in its session. A process that moves to
    a new group afterwards is not reached by it, and is there at the next look.
    """
    return "kill -- " + " ".join(f"-{group}" for group in found.groups or (found.pgid,))


def _look_line(session: int) -> str:
    # ``-s`` and not ``-g``: procps reads a ``-g`` list that is all digits as
    # sessions too, and says in its manual that other ``ps`` read it as groups.
    return f"ps -o pid,pgid,lstart,args -s {session}"


def look_line(found: Survivor) -> str:
    """
    The command that shows what a survivor is, to read before killing: every
    process of the session its shell led.
    """
    return _look_line(found.pid)


def unidentified_line(found: Unidentified) -> str:
    """
    What a record says of processes that may or may not be its command's
    (``End.unconfirmed``). It names how to look and gives nothing to kill with.
    """
    count = f"{found.members} process{'' if found.members == 1 else 'es'}"
    return (
        f"its shell (pid {found.session}) is gone and {count}"
        f" {'is' if found.members == 1 else 'are'} alive in the session number it led."
        " That is what it left running, or another program's: the number is handed out"
        " again once a session has gone, and nothing on record tells the two apart."
        f" To see what is there: {_look_line(found.session)} -- pptmstr names nothing"
        " to kill, because it cannot say this is the command"
    )


# -- the runner -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Ended:
    """
    One command's end, as the callback is given it.

    ``command`` is the command that was handed over with ``end`` set, and
    ``stop_asked_at`` when a stop reached it. ``record`` is what
    ``commandfile.write_end`` answered.

    ``started`` is what ``commandfile.write_started`` answered, and None when
    no process was started. An ``Unwritten`` there cost this command nothing,
    since its end is known; it is what would have left a command that outlived
    pptmstr with no record to be found from, and so it is to be shown.

    ``taken_out`` is ``environment_changes`` for this run in its directory:
    what the shell was not given. Empty when no process was started.
    """

    command: Command
    record: commandfile.Written | commandfile.Unwritten
    started: commandfile.Written | commandfile.Unwritten | None = None
    taken_out: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Unconfirmed:
    """
    A command ``close`` could not confirm dead within its bound. ``pid`` is the
    shell's, and its session's, or None when no process had been started.
    """

    command_id: CommandId
    pid: int | None
    reason: str


NOT_RECORDED_IN_TIME = "its end had not been recorded in time"


class _Output:
    """
    The pipe of one command, moved into its log on a thread of its own.

    The thread owns the pipe and the log and is the only thing that closes
    either, so nothing closes a descriptor under a read or a write. It ends at
    the pipe's end, or at the moment ``finish`` gives it. A write to the log
    that does not return keeps the thread and both descriptors, and nothing
    else: the counts are taken before the write and read under a lock.
    """

    def __init__(
        self, name: str, pipe: IO[bytes], log: io.FileIO, max_log_bytes: int, tail_bytes: int
    ) -> None:
        self._pipe = pipe
        self._log = log
        self._max_log_bytes = max_log_bytes
        self._tail_bytes = tail_bytes
        self._lock = threading.Lock()
        self._total = self._logged = 0
        self._kept = bytearray()
        self._stop_by: float | None = None
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._read, name=name, daemon=True)

    def start(self) -> None:
        """
        ``RuntimeError`` when no thread can be made; the pipe and the log are
        then closed here, nothing else being left to do it.
        """
        try:
            self._thread.start()
        except RuntimeError:
            self._close()
            raise

    def _close(self) -> None:
        for held in (self._pipe, self._log):
            with contextlib.suppress(OSError):
                held.close()
        self._done.set()

    def _read(self) -> None:
        fd = self._pipe.fileno()
        log_ok = True
        # ``poll`` and not ``select``, which cannot take a descriptor numbered
        # past 1023. A hang-up is reported as an event, and read as the end.
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        try:
            while True:
                with self._lock:
                    stop_by = self._stop_by
                if stop_by is not None and time.monotonic() >= stop_by:
                    return
                if not poller.poll(TICK_S * 1000):
                    continue
                chunk = os.read(fd, _CHUNK)
                if not chunk:
                    return
                with self._lock:
                    self._total += len(chunk)
                    self._kept += chunk
                    if len(self._kept) > 2 * self._tail_bytes:
                        del self._kept[: -self._tail_bytes]
                    room = self._max_log_bytes - self._logged
                if log_ok and room > 0:
                    wrote = self._write(chunk[:room])
                    log_ok = wrote == min(room, len(chunk))
                    with self._lock:
                        self._logged += wrote
        finally:
            self._close()

    def _write(self, data: bytes) -> int:
        """
        Put ``data`` in the log and say how many bytes of it went in. A write
        may take fewer than it was given, so it is asked again for the rest;
        one that takes none, or fails, ends it. What is counted as logged is
        what the file holds, which is what decides whether a tail is kept.
        """
        done = 0
        while done < len(data):
            try:
                wrote = self._log.write(data[done:])
            except OSError:
                break
            if not wrote:
                break
            done += wrote
        return done

    def finish(self, within: float) -> bool:
        """
        Have the reading stop ``within`` seconds from now if the pipe has not
        ended by then, and wait for it. False when the thread did not come back,
        which is a write to the log that has not returned.
        """
        with self._lock:
            if self._stop_by is None:
                self._stop_by = time.monotonic() + within
        return self._done.wait(within + 4 * TICK_S)

    def taken(self) -> tuple[int, int, bytes]:
        """
        How many bytes were read, how many of them are in the log, and the last
        ``tail_bytes`` of them.
        """
        with self._lock:
            return self._total, self._logged, bytes(self._kept[-self._tail_bytes :])


@dataclass(slots=True)
class _Job:
    """
    One command in a worker's hands. Everything but ``command`` and the last
    three, which are the worker's alone, is read and written with the runner's
    lock held.

    ``released`` is set before the shell is reaped: from then its pid is not
    this command's, and nothing signals it.
    """

    command: Command
    refusal: str | None
    stop_at: float | None = None
    abandoned: bool = False
    process: subprocess.Popen[bytes] | None = None
    output: _Output | None = None
    released: bool = False
    run_recorded: bool = False
    started: commandfile.Written | commandfile.Unwritten | None = None
    taken_out: tuple[str, ...] = ()

    @property
    def pid(self) -> int | None:
        return None if self.process is None else self.process.pid


def _exited(pid: int) -> bool:
    """
    Whether the child has exited, without reaping it.

    Unreaped, it keeps its pid from being given to another process, and with
    it the number of the session and of the group it leads: a signal sent
    after this says True reaches what is left of this command or nothing.
    """
    try:
        return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except ChildProcessError:
        return True


def _signal_group(pgid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sig)


def _signal_session(sid: int, sig: int) -> None:
    """
    Send ``sig`` to every process group that has a live process of the session
    ``sid``, and to the group ``sid`` itself whether or not it could be seen.
    **Only while the shell that leads the session is unreaped**: that is what
    makes the session, and the shell's own group, this command's.

    By group and not by pid, so that a group is signalled as one thing and a
    process forked inside it mid-way is not missed. A group other than the
    shell's is named by a number read a moment earlier; should it empty and
    its number be given to a new group leader in that moment, the signal goes
    there. That takes the pid counter going all the way round between two
    lines of this function.

    Where ``/proc`` cannot be read the shell's own group is all that is
    reached, and the caller is the one that knows to say so.
    """
    try:
        members = _session(sid)
    except OSError:
        members = None
    for group in sorted({sid, *_groups(members or [])}):
        _signal_group(group, sig)


UNCOUNTED = (
    "what was alive in its session could not be counted -- no /proc, or one that could"
    " not be read -- so only its shell's own process group is known to have been"
    " signalled, and nothing was seen to go but the shell"
)


class Runner:
    """
    Runs commands, a worker thread and a reader thread each. ``start``,
    ``stop``, ``abandon`` and ``close`` may be called from any thread. The first
    two perform no IO; ``abandon`` reads ``/proc`` once and sends signals; none
    of the three waits.

    ``on_end`` is called once for each command ``start`` took, **on that
    command's worker thread**, after its end is written. It must not block for
    long -- the worker's slot for the session is already free, but the thread
    is not -- and whatever it raises goes to ``threading.excepthook``.

    ``root`` is the sessions root, as ``commandfile`` takes it. ``environ`` is
    what ``environment()`` is applied to, read when a command starts; None is
    this process's. ``clock`` gives ``End.at`` and a stop's time; durations and
    the timeout are measured on ``time.monotonic``.

    ``tail_bytes`` is at least 1: what is kept in memory of an output is cut
    back to it, and nothing can be cut back to none.
    """

    def __init__(
        self,
        on_end: Callable[[Ended], None],
        *,
        root: Path | None = None,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.time,
        grace_s: float = KILL_GRACE_S,
        drain_s: float = DRAIN_S,
        max_log_bytes: int = commandfile.MAX_LOG_BYTES,
        tail_bytes: int = commandfile.MAX_TAIL_FILE_BYTES,
    ) -> None:
        if tail_bytes < 1 or tail_bytes > commandfile.MAX_TAIL_FILE_BYTES:
            raise ValueError(
                f"tail_bytes is {tail_bytes!r}: from 1 to {commandfile.MAX_TAIL_FILE_BYTES}"
            )
        if max_log_bytes < 0:
            raise ValueError(f"max_log_bytes is {max_log_bytes!r}")
        self._on_end = on_end
        self._root = root
        self._environ = environ
        self._clock = clock
        self._drain_s = drain_s
        self._max_log_bytes = max_log_bytes
        self._tail_bytes = tail_bytes
        self._changed = threading.Condition(threading.Lock())
        # Read and written only with ``_changed`` held, down to ``_unreaped``.
        self._grace_s = grace_s
        self._closed = False
        self._jobs: dict[CommandId, _Job] = {}
        self._session: dict[str, CommandId] = {}
        self._taken: set[CommandId] = set()
        self._unreaped: list[Unconfirmed] = []

    # -- the owner's side ----------------------------------------------------------

    def start(self, command: Command) -> None:
        """
        Take one approved command and return. Its end comes through ``on_end``.

        ``ValueError`` for a command with no ``run`` or with an ``end``: neither
        is a run being handed over, and an end produced for one would be made
        up. A command id this runner has already taken is ignored.

        After ``close``, and for a session that has a command running, the
        command is still taken: its run is recorded and it ends ``NotStarted``.
        """
        if command.run is None or command.end is not None:
            raise ValueError("a runner is handed a command that has a run and no end")
        with self._changed:
            if command.id in self._taken:
                return
            self._taken.add(command.id)
            # Only the session's slot is settled here. Whether the runner is
            # closed is asked by the worker, once the run is recorded.
            refusal: str | None = None
            if command.session_id in self._session:
                refusal = SESSION_BUSY
            else:
                self._session[command.session_id] = command.id
            job = _Job(command=command, refusal=refusal)
            self._jobs[command.id] = job
        worker = threading.Thread(
            target=self._work, args=(job,), name=f"command-{command.id}", daemon=True
        )
        try:
            worker.start()
        except RuntimeError:
            # No thread could be made. The end is still owed, so it is produced
            # here, on the caller's thread, with the IO that takes.
            job.refusal = job.refusal or "no thread could be started to run it"
            self._work(job)

    def stop(self, command_id: CommandId, at: float | None = None) -> None:
        """
        Ask for a running command's session to be killed, and return. It ends as
        ``Cancelled(OPERATOR)``, or as whatever it had already ended as.

        ``at`` is when the operator asked, and is what the end records as
        ``stop_asked_at``; None is now. An id that is not running, and a second
        stop, do nothing.

        A stop that arrives after the shell has exited by itself, while what
        the shell left behind is being ended, is too late to be what ended the
        command: the outcome stays ``Exited`` and the stop is recorded beside
        it. What it asked for is what is already being done, and the end is
        not reported until it is.
        """
        with self._changed:
            job = self._jobs.get(command_id)
            if job is not None and job.stop_at is None:
                job.stop_at = self._clock() if at is None else at

    def abandon(self, command_id: CommandId) -> bool:
        """
        Give up on a command whose end has been declared without this runner:
        every process group found in its shell's session is sent ``SIGKILL``
        now, from the caller's thread, and its session's slot is free when this
        returns. Finding them reads ``/proc`` once through, with the lock held.

        For the owner that stopped waiting (the store ended it ``EndUnknown``).
        If the worker does come back and a process had been started, the end it
        records is ``EndUnknown`` too, so the file and the store say the same
        thing. Where no process existed yet none is started, and the end it
        records is ``NotStarted``: the file then says more than the store,
        which was told only that nothing had been heard, and it is the file
        that is read at the next start.

        False for an id this runner does not hold -- never taken, or ended
        already -- and nothing is signalled then: there is no process this
        runner can still name.
        """
        with self._changed:
            job = self._jobs.get(command_id)
            if job is None:
                return False
            if job.abandoned:
                return True
            job.abandoned = True
            session = job.command.session_id
            if self._session.get(session) == command_id:
                del self._session[session]
            # With the lock held, so the worker cannot reap the shell -- and
            # free its pid -- between the test and the signal.
            if job.process is not None and not job.released:
                _signal_session(job.process.pid, signal.SIGKILL)
        return True

    def close(self, timeout: float = CLOSE_TIMEOUT) -> tuple[Unconfirmed, ...]:
        """
        Kill every running command's session, wait at most ``timeout`` seconds for
        their ends to be written and delivered, and return what could not be
        confirmed dead.

        Each ends as ``Cancelled(SHUTDOWN)`` unless it had already been stopped
        or timed out. The time between ``SIGTERM`` and ``SIGKILL`` is shortened
        to half of ``timeout`` when that is less, so that the kill fits inside
        the bound. May be called again: it waits again, for whatever is left.
        After the first call ``start`` runs nothing.
        """
        timeout = max(timeout, 0.0)
        with self._changed:
            self._closed = True
            self._grace_s = min(self._grace_s, timeout / 2)
            self._changed.wait_for(lambda: not self._jobs, timeout=timeout)
            left = tuple(
                Unconfirmed(job.command.id, job.pid, NOT_RECORDED_IN_TIME)
                for job in self._jobs.values()
            )
            unreaped = tuple(self._unreaped)
            self._unreaped.clear()
        return unreaped + left

    # -- the worker's side ---------------------------------------------------------

    def _work(self, job: _Job) -> None:
        try:
            end = self._attempt(job)
        except Exception as exc:
            # The one place a failure of the runner itself becomes an end.
            why = f"the runner failed: {type(exc).__name__}: {exc}"
            if job.process is None:
                end = End(outcome=NotStarted(why), at=self._clock())
            else:
                end = End(outcome=EndUnknown(), at=self._clock(), unconfirmed=self._give_up(job))
        with self._changed:
            stop_at = job.stop_at
        command = dataclasses.replace(job.command, end=end, stop_asked_at=stop_at)
        # With no ``run.json`` of this runner's the disk can only take an end
        # that says nothing was approved there, which a ``NotStarted`` may be.
        on_disk = (
            command
            if job.run_recorded
            else dataclasses.replace(command, run=None, stop_asked_at=None)
        )
        record = commandfile.write_end(on_disk, self._root)
        with self._changed:
            del self._jobs[command.id]
            if self._session.get(command.session_id) == command.id:
                del self._session[command.session_id]
            self._changed.notify_all()
        self._on_end(
            Ended(command=command, record=record, started=job.started, taken_out=job.taken_out)
        )

    def _give_up(self, job: _Job) -> str | None:
        """
        Kill the session of a command the worker failed on, without any of the
        steps that may be what failed. Why it was not seen to die, or None.
        """
        process = job.process
        assert process is not None
        with self._changed:
            released = job.released
            # Nothing signals this pid after the wait below has reaped it.
            job.released = True
        unconfirmed: str | None = None
        if not released:
            _signal_session(process.pid, signal.SIGKILL)
            try:
                process.wait(timeout=REAP_S)
            except subprocess.TimeoutExpired:
                unconfirmed = self._not_seen_to_exit(job, process.pid, None)
        if job.output is not None:
            job.output.finish(self._drain_s)
        return unconfirmed

    def _asked(self, job: _Job) -> Outcome | None:
        """
        What the worker has been asked to end the command as, or None.
        """
        with self._changed:
            if job.abandoned:
                return EndUnknown()
            if job.stop_at is not None:
                return Cancelled(CancelledBy.OPERATOR)
            if self._closed:
                return Cancelled(CancelledBy.SHUTDOWN)
        return None

    def _attempt(self, job: _Job) -> End:
        command = job.command
        run = command.run
        assert run is not None

        written = commandfile.write_run(command, self._root)
        if isinstance(written, commandfile.Unwritten):
            return self._not_started(f"the run could not be recorded: {written.reason}")
        job.run_recorded = True
        if job.refusal is not None:
            return self._not_started(job.refusal)
        asked = self._asked(job)
        if asked is not None:
            return self._not_started(_before_it_started(asked))
        if not os.path.isdir(run.cwd):
            return self._not_started(f"the directory {run.cwd} is not there")
        problem = _unusable(run.shell)
        if problem is not None:
            return self._not_started(f"the shell {run.shell} {problem}")

        log = commandfile.open_log(command.session_id, command.id, self._root)
        if isinstance(log, commandfile.Unwritten):
            return self._not_started(f"{commandfile.LOG_NAME} could not be created: {log.reason}")
        try:
            taken_out = environment_changes(self._environ, run.cwd)
            env = environment(self._environ, run.cwd)
            # Asked a second time, as the last thing before the process: every
            # step since the first asking reads or writes a disk, and may have
            # taken any length of time.
            asked = self._asked(job)
            if asked is not None:
                log.close()
                return self._not_started(_before_it_started(asked))
            process = subprocess.Popen(
                [run.shell, "-l", "-c", run.text],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=run.cwd,
                env=env,
                bufsize=0,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            log.close()
            return self._not_started(f"{type(exc).__name__}: {exc}")
        except BaseException:
            log.close()
            raise
        assert process.stdout is not None
        output = _Output(
            f"command-output-{command.id}",
            process.stdout,
            log,
            self._max_log_bytes,
            self._tail_bytes,
        )
        job.taken_out = taken_out
        with self._changed:
            job.process = process
            job.output = output
            # An ``abandon`` that came while ``Popen`` was at work found no
            # process to kill, and nothing else kills for it at once. Either it
            # saw the process or this sees it: the lock is what orders the two.
            if job.abandoned:
                _signal_session(process.pid, signal.SIGKILL)
        began = time.monotonic()
        # Before anything else is done with the process. Not needed to run it,
        # so a failure here stops nothing: the record is then one a survivor
        # cannot be found from, and ``Ended.started`` says so.
        job.started = commandfile.write_started(
            command.session_id, command.id, started_now(process.pid, self._clock()), self._root
        )
        output.start()
        return self._follow(job, process, output, began, run.timeout_s)

    def _not_started(self, reason: str) -> End:
        return End(outcome=NotStarted(reason), at=self._clock())

    def _not_seen_to_exit(self, job: _Job, pid: int, left: int | None) -> str:
        with self._changed:
            self._unreaped.append(Unconfirmed(job.command.id, pid, "sent SIGKILL and not gone"))
        what = (
            f"the shell (pid {pid})"
            if left is None
            else f"{left} process{'' if left == 1 else 'es'} of it"
        )
        return (
            f"every process group found in its session {pid} was sent SIGKILL, and {what} had"
            f" not gone {REAP_S:g} seconds later"
        )

    def _wind_down(self, job: _Job, pid: int) -> str | None:
        """
        End what is alive of the shell's session: ``SIGTERM`` to every group
        of it, the grace, then ``SIGKILL`` to whatever groups it still has,
        again at each look until it has none. None once the session is seen
        to be empty, and otherwise why it was not.

        The shell is unreaped throughout, so every signal here reaches this
        command's session or nothing.
        """
        _signal_session(pid, signal.SIGTERM)
        with self._changed:
            kill_at = time.monotonic() + self._grace_s
        while True:
            now = time.monotonic()
            with self._changed:
                # ``close`` may have shortened the grace since, and an owner
                # that gave up has no use for one.
                kill_at = now if job.abandoned else min(kill_at, now + self._grace_s)
            if session_members(pid) == ():
                return None
            if now >= kill_at:
                break
            time.sleep(TICK_S)
        reap_by = time.monotonic() + REAP_S
        while True:
            # Each time round: a process may have made itself a group between
            # the last listing and the last signal.
            _signal_session(pid, signal.SIGKILL)
            members = session_members(pid)
            if members == ():
                return None
            # Where the session cannot be counted the shell is what can be
            # seen, and its going is all that was seen.
            if members is None and _exited(pid):
                return UNCOUNTED
            if time.monotonic() >= reap_by:
                return self._not_seen_to_exit(job, pid, None if members is None else len(members))
            time.sleep(TICK_S)

    def _follow(
        self,
        job: _Job,
        process: subprocess.Popen[bytes],
        output: _Output,
        began: float,
        timeout_s: float,
    ) -> End:
        """
        Wait and kill until nothing of the command is left, and say how it ended.
        """
        pid = process.pid
        forced: Outcome | None = None
        while not _exited(pid):
            forced = self._asked(job)
            if forced is None and time.monotonic() - began >= timeout_s:
                forced = TimedOut()
            if forced is not None:
                break
            time.sleep(TICK_S)

        leftover: int | None = None
        unconfirmed: str | None = None
        if forced is None:
            members = session_members(pid)
            leftover = None if members is None else len(members)
            if leftover != 0:
                unconfirmed = self._wind_down(job, pid)
        else:
            unconfirmed = self._wind_down(job, pid)
        output.finish(self._drain_s)

        code: int | None = None
        if _exited(pid):
            with self._changed:
                job.released = True
            code = process.wait()
        with self._changed:
            if job.abandoned:
                forced, leftover = EndUnknown(), None
        outcome: Outcome
        if forced is not None:
            outcome = forced
        else:
            assert code is not None
            outcome = Exited(code)
        duration = time.monotonic() - began
        total, logged, tail = output.taken()
        if total > logged:
            commandfile.write_tail(job.command.session_id, job.command.id, tail, self._root)
        return End(
            outcome=outcome,
            at=self._clock(),
            duration_s=duration,
            output_bytes=total,
            tail=cut_tail(tail),
            leftover=leftover,
            unconfirmed=unconfirmed,
        )


def _before_it_started(asked: Outcome) -> str:
    """
    Why a command that was asked to end before any process existed was not started.
    """
    if isinstance(asked, EndUnknown):
        return "pptmstr had stopped waiting for it before it was started"
    if asked == Cancelled(CancelledBy.OPERATOR):
        return "stopped before it was started"
    return RUNNER_CLOSED
