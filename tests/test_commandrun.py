"""
The break-glass runner, against real processes.

Nothing here reads the operator's profile. Commands are given to a **fake
shell**: a script under ``tmp_path`` that writes its own argument vector,
NUL-separated, to a file and then becomes ``/bin/sh -c "$3"``. So ``-l`` is
honoured as an argument and never acted on, what the shell was handed is on disk
to compare byte for byte, and the group kill, the timeout, the pipe and the
escape are the kernel's.

Every process a test starts carries a mark in its environment that is the
test's alone. After each test nothing with that mark may be alive, and the test
process may have no child it did not have before -- including a child that left
its group, which the first check finds and the second cannot.
"""

from __future__ import annotations

import contextlib
import dataclasses
import errno
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pptmstr import commandfile, commandrun
from pptmstr.command import (
    MAX_TAIL_CHARS,
    MAX_TAIL_LINES,
    Cancelled,
    CancelledBy,
    Command,
    End,
    EndUnknown,
    Exited,
    NotStarted,
    Run,
    Survivor,
    TimedOut,
)
from pptmstr.commandrun import (
    Ended,
    Gone,
    NoShell,
    NotLooked,
    Runner,
    Shell,
    Unconfirmed,
    Unidentified,
)

MARK = "PPTMSTR_TEST_COMMANDRUN"

FAKE_SHELL = """#!/bin/sh
printf '%s\\0' "$0" "$@" > "$ARGV_OUT"
exec /bin/sh -c "$3"
"""


# -- what is alive ----------------------------------------------------------------


def _state(pid: int) -> str | None:
    """
    The process's state letter, or None when there is no such process.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return stat.rsplit(")", 1)[1].split()[0]


def _alive(pid: int) -> bool:
    return _state(pid) not in (None, "Z", "X")


def _gone(pids: list[int], within: float = 3.0) -> bool:
    deadline = time.monotonic() + within
    while any(_alive(pid) for pid in pids):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def _marked(mark: str) -> list[int]:
    wanted = f"{MARK}={mark}".encode()
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            environ = Path(f"/proc/{entry}/environ").read_bytes()
        except OSError:
            continue
        if wanted in environ.split(b"\0"):
            found.append(int(entry))
    return found


def _children() -> set[int]:
    mine = set()
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                stat = Path(f"/proc/{entry}/stat").read_text()
            except OSError:
                continue
            if int(stat.rsplit(")", 1)[1].split()[1]) == os.getpid():
                mine.add(int(entry))
    return mine


@pytest.fixture(autouse=True)
def mark() -> Iterator[str]:
    """
    This test's mark, and the check that nothing it started outlives it.
    """
    before = _children()
    value = uuid.uuid4().hex
    yield value
    deadline = time.monotonic() + 2.0
    while (_marked(value) or _children() - before) and time.monotonic() < deadline:
        time.sleep(0.02)
    left, children = _marked(value), _children() - before
    for pid in left:
        os.kill(pid, signal.SIGKILL)
    assert not left, f"processes left running: {left}"
    assert not children, f"children of the test process left, reaped or not: {children}"


# -- the harness ------------------------------------------------------------------


class Harness:
    def __init__(self, tmp_path: Path, mark: str, **options: Any) -> None:
        self.root = tmp_path / "sessions"
        self.cwd = tmp_path / "cwd"
        self.cwd.mkdir()
        self.argv_out = tmp_path / "argv"
        self.shell = tmp_path / "fake-shell"
        self.shell.write_text(FAKE_SHELL)
        self.shell.chmod(0o755)
        self.ended: list[Ended] = []
        self.threads: list[int] = []
        self._cond = threading.Condition()
        # Of this harness alone: a process is found by the id it carries, and
        # another test's must not be found by this one's.
        self._ids = uuid.uuid4().hex[:8]
        self._serial = 0
        self.environ = {**os.environ, MARK: mark, "ARGV_OUT": str(self.argv_out)}
        self.runner = Runner(self._on_end, root=self.root, environ=self.environ, **options)

    def _on_end(self, ended: Ended) -> None:
        with self._cond:
            self.ended.append(ended)
            self.threads.append(threading.get_ident())
            self._cond.notify_all()

    def command(
        self, text: str, *, session: str = "s1", timeout_s: float = 30.0, request: bool = True
    ) -> Command:
        self._serial += 1
        asked = Command(
            id=f"cmd-20261013T101500Z-{self._ids}{self._serial:04x}",
            asker=(session, None),
            # Never the string that runs: the operator's edit is what the shell gets.
            asked=f"asked, not run: {text}"[:50],
            reason="",
            expects="",
            asked_at=100.0,
        )
        if request:
            assert isinstance(commandfile.write_request(asked, self.root), commandfile.Written)
        run = Run(
            text=text,
            shell=str(self.shell),
            cwd=str(self.cwd),
            timeout_s=timeout_s,
            started_at=101.0,
        )
        return dataclasses.replace(asked, run=run)

    def start(self, text: str, **options: Any) -> Command:
        command = self.command(text, **options)
        self.runner.start(command)
        return command

    def wait(self, command: Command, within: float = 20.0) -> Ended:
        with self._cond:
            ok = self._cond.wait_for(
                lambda: any(e.command.id == command.id for e in self.ended), within
            )
            assert ok, f"no end for {command.id} within {within}s"
            (ended,) = [e for e in self.ended if e.command.id == command.id]
        return ended

    def run(self, text: str, **options: Any) -> Ended:
        return self.wait(self.start(text, **options))

    def directory(self, command: Command) -> Path:
        found = commandfile.command_dir(command.session_id, command.id, self.root)
        assert found is not None
        return found

    def log(self, command: Command) -> bytes:
        return (self.directory(command) / "output.log").read_bytes()

    def pids(self, command: Command, *names: str, within: float = 10.0) -> list[int]:
        """
        The pids a running command has printed as ``<name> <pid>`` lines.
        """
        deadline = time.monotonic() + within
        while True:
            try:
                lines = self.log(command).decode(errors="replace").splitlines()
            except OSError:
                lines = []
            words = dict(pair for pair in (line.split() for line in lines) if len(pair) == 2)
            if all(name in words for name in names):
                return [int(words[name]) for name in names]
            assert time.monotonic() < deadline, f"{names} not printed: {words}"
            time.sleep(0.01)


@pytest.fixture
def h(tmp_path: Path, mark: str) -> Iterator[Harness]:
    harness = Harness(tmp_path, mark, grace_s=0.4, drain_s=0.3)
    yield harness
    assert harness.runner.close(10.0) == ()


def _outcome(ended: Ended) -> Any:
    assert ended.command.end is not None
    return ended.command.end.outcome


def _end(ended: Ended) -> End:
    assert ended.command.end is not None
    return ended.command.end


# A command that prints its shell's pid and a child's, and then waits on both.
TREE = "sh -c 'echo child $$; exec sleep 1000' & echo shell $$; wait"
# What coreutils ``timeout`` does to what it runs, and a shell with job control
# to each job: a process group of its own, inside the shell's session. ``inner``
# is in that group, and a signal to the shell's group does not reach it.
OWN_GROUP = "echo shell $$; timeout 600 sh -c 'echo inner $$; exec sleep 1000'; echo after"
DEAF_TREE = (
    "trap '' TERM; sh -c 'trap \"\" TERM; echo child $$; sleep 1000 & echo grandchild $!; wait' &"
    " echo shell $$; wait"
)


# -- the property: the shell receives the approved string -------------------------

_CORPUS = [
    "make install",
    "echo 'single' \"double\" `tick` $(sub) ${HOME} $1 \\ \\\\ \\n",
    "printf '%s\\n' one\nprintf '%s\\n' two\n",
    "  leading and trailing spaces  ",
    "echo é中\U0001f600 ‮​ bidi and zero width",
    "echo tab\there; echo cr\rhere; echo bell\x07",
    "true # " + "x" * 1_993,
    "true && false || echo '-l' -c -- ; :",
    "*",
]


@pytest.mark.parametrize("text", _CORPUS, ids=range(len(_CORPUS)))
def test_the_shell_is_given_exactly_the_approved_string(h: Harness, text: str) -> None:
    command = h.command(text)
    assert command.edited
    h.runner.start(command)
    ended = h.wait(command)
    assert not isinstance(_outcome(ended), NotStarted), _outcome(ended)
    argv = h.argv_out.read_bytes().split(b"\0")
    assert argv == [os.fsencode(h.shell), b"-l", b"-c", text.encode("utf-8"), b""]


def test_the_corpus_has_a_command_of_the_longest_length_allowed() -> None:
    from pptmstr.command import MAX_COMMAND_CHARS, command_refusal

    assert max(len(text) for text in _CORPUS) == MAX_COMMAND_CHARS
    assert all(command_refusal(text) is None for text in _CORPUS)


def test_popen_is_given_an_argument_list_and_no_shell(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    real = subprocess.Popen

    def popen(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    command = h.command("echo \"$0\" ';' rm -rf /")
    h.runner.start(command)
    h.wait(command)
    ((args, kwargs),) = calls
    assert args == ([str(h.shell), "-l", "-c", "echo \"$0\" ';' rm -rf /"],)
    assert "shell" not in kwargs and "executable" not in kwargs
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["start_new_session"] is True
    assert kwargs["cwd"] == str(h.cwd)


def test_a_real_bash_given_dash_l_is_a_login_shell(h: Harness, tmp_path: Path) -> None:
    """
    The one test with a real shell. ``HOME`` is a fresh directory, so the only
    profile read is the system's.
    """
    if not os.access("/bin/bash", os.X_OK):
        pytest.skip("no /bin/bash")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text("echo profile-was-read\n")
    h.environ["HOME"] = str(home)
    command = h.command("shopt -q login_shell && echo is-login; echo 'a  b' \"$0\"")
    assert command.run is not None
    command = dataclasses.replace(command, run=dataclasses.replace(command.run, shell="/bin/bash"))
    h.runner.start(command)
    ended = h.wait(command)
    assert _outcome(ended) == Exited(0)
    lines = h.log(command).decode().splitlines()
    assert lines[-3:] == ["profile-was-read", "is-login", "a  b /bin/bash"], lines


# -- the property: nothing starts before the run is recorded ----------------------


def test_the_run_is_on_disk_before_the_process_exists(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    real_write, real_popen = commandfile.write_run, subprocess.Popen
    command = h.command("echo ran")

    def write_run(*args: Any, **kwargs: Any) -> Any:
        order.append("write_run:in")
        result = real_write(*args, **kwargs)
        order.append("write_run:out")
        return result

    def popen(*args: Any, **kwargs: Any) -> Any:
        recorded = json.loads((h.directory(command) / "run.json").read_text())
        order.append(f"popen:{recorded['text']}")
        assert not h.argv_out.exists()
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(commandfile, "write_run", write_run)
    monkeypatch.setattr(subprocess, "Popen", popen)
    h.runner.start(command)
    assert _outcome(h.wait(command)) == Exited(0)
    assert order == ["write_run:in", "write_run:out", "popen:echo ran"]


@pytest.mark.parametrize("why", ["no request", "already recorded", "refused"])
def test_a_run_that_is_not_recorded_starts_nothing(
    h: Harness, monkeypatch: pytest.MonkeyPatch, why: str
) -> None:
    started: list[object] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    command = h.command("echo ran", request=why != "no request")
    if why == "already recorded":
        assert isinstance(commandfile.write_run(command, h.root), commandfile.Written)
    if why == "refused":
        monkeypatch.setattr(
            commandfile, "write_run", lambda *a, **k: commandfile.Unwritten("disk full")
        )
    h.runner.start(command)
    ended = h.wait(command)
    outcome = _outcome(ended)
    assert isinstance(outcome, NotStarted) and "could not be recorded" in outcome.reason
    assert started == [] and not h.argv_out.exists()
    assert not (h.root / "s1" / "commands" / command.id / "output.log").exists()
    # What was delivered still says the operator approved it.
    assert ended.command.run == command.run
    if why == "refused":
        # No run on disk, so the end there is the one that needs none.
        assert isinstance(ended.record, commandfile.Written)
        read = commandfile.read_command("s1", command.id, h.root)
        assert isinstance(read, Command) and read.run is None and read.end == ended.command.end
    else:
        assert isinstance(ended.record, commandfile.Unwritten)


def test_a_command_that_cannot_be_started_is_not_started_and_says_which(
    h: Harness, tmp_path: Path
) -> None:
    gone = h.command("echo ran", session="a")
    assert gone.run is not None
    gone = dataclasses.replace(gone, run=dataclasses.replace(gone.run, cwd=str(tmp_path / "gone")))
    plain = tmp_path / "not-executable"
    plain.write_text("#!/bin/sh\n")
    no_shell = h.command("echo ran", session="b")
    assert no_shell.run is not None
    no_shell = dataclasses.replace(
        no_shell, run=dataclasses.replace(no_shell.run, shell=str(plain))
    )
    reasons = (
        (gone, f"the directory {tmp_path / 'gone'} is not there"),
        (no_shell, f"the shell {plain} is not executable"),
    )
    for command, reason in reasons:
        h.runner.start(command)
        ended = h.wait(command)
        assert _outcome(ended) == NotStarted(reason)
        assert isinstance(ended.record, commandfile.Written)
        assert commandfile.read_command(command.session_id, command.id, h.root) == ended.command
    assert not h.argv_out.exists()


def test_a_popen_that_fails_is_not_started(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def popen(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(subprocess, "Popen", popen)
    ended = h.run("echo ran")
    outcome = _outcome(ended)
    assert outcome == NotStarted("PermissionError: [Errno 13] Permission denied")
    assert commandfile.read_command("s1", ended.command.id, h.root) == ended.command


# -- how it ends ------------------------------------------------------------------


def test_an_exit_is_recorded_with_its_code_its_output_and_its_time(h: Harness) -> None:
    ended = h.run("echo out; echo err >&2; exit 3")
    end = _end(ended)
    assert end.outcome == Exited(3)
    assert end.tail == "out\nerr\n" and end.output_bytes == 8
    assert 0 <= end.duration_s < 10
    assert h.log(ended.command) == b"out\nerr\n"
    assert not (h.directory(ended.command) / "output.tail").exists()
    assert isinstance(ended.record, commandfile.Written)
    assert commandfile.read_command("s1", ended.command.id, h.root) == ended.command
    assert sorted(os.listdir(h.directory(ended.command))) == [
        "end.json",
        "output.log",
        "request.json",
        "run.json",
        "started.json",
    ]


def test_a_shell_killed_by_a_signal_of_its_own_exited_with_that(h: Harness) -> None:
    assert _outcome(h.run("kill -KILL $$")) == Exited(-9)


def test_standard_input_is_closed_and_the_directory_is_the_runs(h: Harness) -> None:
    ended = h.run("cat; echo eof-seen; pwd")
    assert _outcome(ended) == Exited(0)
    assert _end(ended).tail == f"eof-seen\n{h.cwd}\n"


def test_the_command_does_not_see_pptmstrs_virtualenv(h: Harness, tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    h.environ["VIRTUAL_ENV"] = str(venv)
    h.environ["PATH"] = f"{venv}/bin{os.pathsep}{h.environ['PATH']}"
    h.environ["KEPT"] = "kept"
    ended = h.run('echo "${VIRTUAL_ENV-unset} $KEPT"; echo "$PATH"')
    assert _end(ended).tail == f"unset kept\n{os.environ['PATH']}\n"


def test_a_timeout_kills_the_shell_and_its_child(h: Harness) -> None:
    command = h.start(TREE, timeout_s=0.5)
    pids = h.pids(command, "shell", "child")
    assert all(_alive(pid) for pid in pids)
    ended = h.wait(command)
    assert _outcome(ended) == TimedOut()
    assert 0.5 <= _end(ended).duration_s < 5
    assert _gone(pids)
    assert commandfile.read_command("s1", command.id, h.root) == ended.command


def test_a_group_that_ignores_term_is_killed_after_the_grace(h: Harness) -> None:
    command = h.start(DEAF_TREE, timeout_s=0.3)
    pids = h.pids(command, "shell", "child", "grandchild")
    ended = h.wait(command)
    assert _outcome(ended) == TimedOut()
    # The timeout, then the whole of the grace: nothing in the group took TERM.
    assert 0.3 + 0.4 <= _end(ended).duration_s < 5
    assert _gone(pids)


def test_term_reaches_the_whole_group_and_not_only_the_shell(h: Harness) -> None:
    """
    The shell here ignores TERM and its child does not, so the child's trap runs
    only if the signal was sent to the group; the KILL after the grace would
    end both without a word.
    """
    command = h.start(
        # The child first: a shell cannot trap a signal it inherited as ignored.
        "sh -c 'trap \"echo child-got-term; exit 0\" TERM; echo child $$; sleep 1000 & wait' &"
        " trap '' TERM; echo shell $$; wait"
    )
    pids = h.pids(command, "shell", "child")
    h.runner.stop(command.id)
    ended = h.wait(command)
    assert _outcome(ended) == Cancelled(CancelledBy.OPERATOR)
    assert b"child-got-term\n" in h.log(command)
    assert _gone(pids)


def test_a_child_that_ignores_term_does_not_outlive_a_shell_that_took_it(h: Harness) -> None:
    command = h.start(
        "sh -c 'trap \"\" TERM; echo child $$; sleep 1000 & wait' & echo shell $$; wait"
    )
    pids = h.pids(command, "shell", "child")
    h.runner.stop(command.id)
    ended = h.wait(command)
    assert _outcome(ended) == Cancelled(CancelledBy.OPERATOR)
    assert _gone(pids)


def test_stop_kills_the_group_and_is_recorded_with_when_it_was_asked(h: Harness) -> None:
    command = h.start(TREE)
    pids = h.pids(command, "shell", "child")
    h.runner.stop(command.id, at=555.5)
    h.runner.stop(command.id, at=999.0)
    ended = h.wait(command)
    assert _outcome(ended) == Cancelled(CancelledBy.OPERATOR)
    assert ended.command.stop_asked_at == 555.5
    assert _gone(pids)
    read = commandfile.read_command("s1", command.id, h.root)
    assert read == ended.command
    raw = json.loads((h.directory(command) / "end.json").read_text())
    assert raw["stop_asked_at"] == 555.5 and raw["outcome"] == {
        "kind": "cancelled",
        "by": "operator",
    }


def test_a_stop_of_nothing_running_does_nothing(h: Harness) -> None:
    h.runner.stop("cmd-20261013T101500Z-ffffffffffff")
    ended = h.run("echo fine")
    h.runner.stop(ended.command.id)
    assert _outcome(ended) == Exited(0) and ended.command.stop_asked_at is None
    assert len(h.ended) == 1


def test_a_command_that_exits_by_itself_is_exited_whatever_was_asked_after(h: Harness) -> None:
    ended = h.run("exit 7")
    h.runner.stop(ended.command.id)
    time.sleep(0.2)
    assert [_outcome(e) for e in h.ended] == [Exited(7)]


def test_a_process_that_left_the_group_holding_the_pipe_is_not_waited_for(h: Harness) -> None:
    began = time.monotonic()
    command = h.start("setsid sleep 1000 & echo held $!; echo done")
    (held,) = h.pids(command, "held")
    ended = h.wait(command)
    took = time.monotonic() - began
    try:
        assert _outcome(ended) == Exited(0)
        # The drain, and not the sleep.
        assert 0.3 <= _end(ended).duration_s < 5 and took < 6
        assert _end(ended).tail.endswith("done\n")
        # It is not in the command's session: not counted, and not ended.
        assert _end(ended).leftover == 0 and _alive(held)
    finally:
        os.kill(held, signal.SIGKILL)


def test_a_child_that_leaves_the_session_survives_the_kill(h: Harness) -> None:
    """
    What is not guaranteed, shown: ``setsid`` takes a process out of the
    session, and the session is all a timeout, a stop or a quit can signal.
    """
    command = h.start(
        "setsid sleep 1000 >/dev/null 2>&1 & echo escaped $!; echo shell $$; sleep 1000",
        timeout_s=0.4,
    )
    escaped, shell = h.pids(command, "escaped", "shell")
    ended = h.wait(command)
    try:
        assert _outcome(ended) == TimedOut()
        assert _gone([shell])
        time.sleep(0.3)
        assert _alive(escaped), "the escapee was killed, and the module says it is not"
        assert os.getsid(escaped) != shell
    finally:
        os.kill(escaped, signal.SIGKILL)


# -- the property: the whole group is given the whole grace ------------------------

# Takes TERM, works for a while, and only then says so and exits: what a build
# that removes its lock file on the way out does.
SLOW_TO_CLEAN = """
import os, signal, sys, time

def cleanup(signum, frame):
    time.sleep(float(sys.argv[2]))
    with open(sys.argv[1], "w") as out:
        out.write("cleaned")
    os._exit(0)

signal.signal(signal.SIGTERM, cleanup)
print("child", os.getpid(), flush=True)
time.sleep(1000)
"""


def _slow_to_clean(h: Harness, takes: float) -> tuple[str, Path]:
    script = h.cwd / "slow_to_clean.py"
    script.write_text(SLOW_TO_CLEAN)
    cleaned = h.cwd / "cleaned"
    return f"{sys.executable} {script} {cleaned} {takes}", cleaned


@pytest.mark.parametrize("how", ["stop", "timeout", "close"])
def test_a_child_of_a_shell_that_dies_of_term_is_given_the_whole_grace(
    h: Harness, how: str
) -> None:
    """
    The shell has no trap and dies of TERM at once. Its child takes half the
    grace to clean up, and is left to.
    """
    child, cleaned = _slow_to_clean(h, 0.2)
    command = h.start(f"echo shell $$; cd . && {child}", timeout_s=1.0 if how == "timeout" else 30)
    shell, pid = h.pids(command, "shell", "child")
    assert shell != pid, "the shell became the child, and this is about one that did not"
    if how == "stop":
        h.runner.stop(command.id)
    elif how == "close":
        assert h.runner.close(10.0) == ()
    ended = h.wait(command)
    assert (
        type(_outcome(ended)) is {"stop": Cancelled, "timeout": TimedOut, "close": Cancelled}[how]
    )
    assert cleaned.exists(), "the child was killed before it had cleaned up"
    assert _gone([shell, pid])
    assert _end(ended).unconfirmed is None


def test_the_grace_ends_when_the_group_is_gone_and_not_before(h: Harness) -> None:
    child, cleaned = _slow_to_clean(h, 0.1)
    command = h.start(f"echo shell $$; cd . && {child}")
    h.pids(command, "shell", "child")
    began = time.monotonic()
    h.runner.stop(command.id)
    h.wait(command)
    # Not the whole grace of 0.4 s and the drain after it: the group went by itself.
    assert cleaned.exists() and time.monotonic() - began < 0.4


def test_a_child_slower_than_the_grace_is_killed_at_its_end(h: Harness) -> None:
    child, cleaned = _slow_to_clean(h, 30.0)
    command = h.start(f"echo shell $$; cd . && {child}")
    pids = h.pids(command, "shell", "child")
    began = time.monotonic()
    h.runner.stop(command.id)
    ended = h.wait(command)
    assert 0.4 <= time.monotonic() - began < 5
    assert _outcome(ended) == Cancelled(CancelledBy.OPERATOR)
    assert not cleaned.exists() and _gone(pids)


# -- the property: when a command has ended, nothing of its group is left ---------


def test_what_a_command_left_in_the_background_is_ended_with_it(h: Harness) -> None:
    began = time.monotonic()
    command = h.start("sleep 1000 & echo held $!; sleep 1000 & echo other $!; echo done")
    held = h.pids(command, "held", "other")
    ended = h.wait(command)
    assert _outcome(ended) == Exited(0)
    assert _end(ended).leftover == 2 and _end(ended).unconfirmed is None
    assert _gone(held, within=0.0), "the end was reported with the group still alive"
    assert time.monotonic() - began < 5
    assert _end(ended).tail.endswith("done\n")
    read = commandfile.read_command("s1", command.id, h.root)
    assert read == ended.command
    raw = json.loads((h.directory(command) / "end.json").read_text())
    assert raw["leftover"] == 2


def test_a_command_that_leaves_nothing_says_so_and_is_not_made_to_wait(h: Harness) -> None:
    began = time.monotonic()
    ended = h.run("echo a | cat; (echo b); echo c")
    assert _outcome(ended) == Exited(0) and _end(ended).leftover == 0
    # Neither the grace nor the drain: nothing was left and the pipe was at its end.
    assert time.monotonic() - began < 0.3 + 0.4


def test_what_was_left_in_the_background_is_given_the_grace_to_clean_up(h: Harness) -> None:
    child, cleaned = _slow_to_clean(h, 0.2)
    command = h.start(f"{child} & echo shell $$; sleep 0.3")
    (pid,) = h.pids(command, "child")
    ended = h.wait(command)
    assert _outcome(ended) == Exited(0) and _end(ended).leftover == 1
    assert cleaned.exists() and _gone([pid], within=0.0)


def test_a_leftover_deaf_to_term_is_killed_and_the_end_waits_for_it(h: Harness) -> None:
    command = h.start("sh -c 'trap \"\" TERM; echo deaf $$; exec sleep 1000' & sleep 0.3")
    (deaf,) = h.pids(command, "deaf")
    ended = h.wait(command)
    assert _outcome(ended) == Exited(0) and _end(ended).leftover == 1
    assert 0.3 + 0.4 <= _end(ended).duration_s < 5
    assert _gone([deaf], within=0.0)


def test_a_stop_while_the_leftovers_are_being_ended_is_recorded_and_nothing_is_left(
    h: Harness,
) -> None:
    """
    The stop is too late to be what ended the shell, so the outcome is the
    exit. What it asked for -- nothing of the command left -- is already
    happening, and has happened by the time the end is reported.
    """
    command = h.start("sh -c 'trap \"\" TERM; echo deaf $$; exec sleep 1000' & echo done")
    (deaf,) = h.pids(command, "deaf")
    time.sleep(0.15)
    assert h.ended == [], "the command ended before its leftovers had been given the grace"
    h.runner.stop(command.id, at=42.0)
    ended = h.wait(command)
    assert _outcome(ended) == Exited(0) and ended.command.stop_asked_at == 42.0
    assert _gone([deaf], within=0.0)


# -- the property: output handling cannot block the timeout or a stop -------------


class _StuckLog(io.FileIO):
    """An ``output.log`` on a mount that has stopped answering."""

    release = threading.Event()

    def write(self, data: Any) -> int:
        self.release.wait()
        return len(data)


@pytest.fixture
def stuck_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    real = commandfile.open_log
    _StuckLog.release = threading.Event()

    def open_log(*args: Any, **kwargs: Any) -> Any:
        log = real(*args, **kwargs)
        assert isinstance(log, io.FileIO)
        fd = os.dup(log.fileno())
        log.close()
        return _StuckLog(fd, "wb")

    monkeypatch.setattr(commandfile, "open_log", open_log)
    yield
    _StuckLog.release.set()


@pytest.mark.parametrize("how", ["timeout", "stop"])
def test_a_log_that_cannot_be_written_does_not_hold_the_timeout_or_a_stop(
    h: Harness, stuck_log: None, how: str
) -> None:
    pids_out = h.cwd / "pids"
    command = h.start(
        f"echo $$ > {pids_out}; echo this write never returns; sleep 1000",
        timeout_s=0.5 if how == "timeout" else 30,
    )
    deadline = time.monotonic() + 10
    while not pids_out.exists() or not pids_out.read_text().strip():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    shell = int(pids_out.read_text())
    time.sleep(0.2)
    if how == "stop":
        h.runner.stop(command.id)
    ended = h.wait(command, within=8.0)
    assert type(_outcome(ended)) is (TimedOut if how == "timeout" else Cancelled)
    assert _gone([shell])
    # The slot is free: the session can be given its next command.
    _StuckLog.release.set()
    assert _outcome(h.run("echo next")) == Exited(0)


def test_the_two_ways_a_wind_down_can_be_cut_short_are_not_the_shell_being_gone(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Where the session cannot be counted there is nothing to cut the grace
    short on, and it is taken whole -- after a stop, and after an exit. And the
    end does not say that what it could not count was seen to go.
    """
    monkeypatch.setattr(commandrun, "_session", lambda sid: None)
    child, cleaned = _slow_to_clean(h, 0.2)
    command = h.start(f"echo shell $$; cd . && {child}")
    pids = h.pids(command, "shell", "child")
    began = time.monotonic()
    h.runner.stop(command.id)
    ended = h.wait(command)
    assert 0.4 <= time.monotonic() - began < 5
    assert cleaned.exists() and _gone(pids)
    assert _end(ended).unconfirmed == commandrun.UNCOUNTED

    command = h.start("sleep 1000 & echo held $!; echo done", session="s2")
    held = h.pids(command, "held")
    ended = h.wait(command)
    assert _outcome(ended) == Exited(0) and _end(ended).leftover is None
    assert 0.4 <= _end(ended).duration_s < 5 and _gone(held, within=0.0)
    assert _end(ended).unconfirmed == commandrun.UNCOUNTED


@pytest.mark.parametrize("failure", ["no /proc", "unreadable"])
def test_where_the_session_cannot_be_counted_only_the_shells_group_is_ended_and_the_end_says_so(
    h: Harness, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """
    The limit, shown: with nothing to list the session from, a process in a
    group of its own is not reached, and the end is not one that says nothing
    is left.
    """

    def unlisted(sid: int) -> None:
        if failure == "unreadable":
            raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(commandrun, "_session", unlisted)
    command = h.start(OWN_GROUP)
    shell, inner = h.pids(command, "shell", "inner")
    h.runner.stop(command.id)
    ended = h.wait(command)
    try:
        assert _outcome(ended) == Cancelled(CancelledBy.OPERATOR) and _gone([shell])
        assert _alive(inner), "it was reached, and the module says it cannot be"
        assert _end(ended).unconfirmed == commandrun.UNCOUNTED
        assert any("NOT CONFIRMED DEAD" in remark for remark in _end(ended).remarks)
    finally:
        os.killpg(os.getpgid(inner), signal.SIGKILL)


def test_a_group_not_seen_to_go_after_kill_is_not_recorded_as_plainly_ended(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A process SIGKILL does not end cannot be made here, so the observation is
    what is faked: the group is reported as one process that never goes.
    """
    monkeypatch.setattr(commandrun, "REAP_S", 0.3)
    monkeypatch.setattr(commandrun, "session_members", lambda sid: (sid,))
    command = h.start("echo shell $$; sleep 1000", timeout_s=0.2)
    (shell,) = h.pids(command, "shell")
    ended = h.wait(command)
    end = _end(ended)
    assert end.outcome == TimedOut()
    assert end.unconfirmed is not None and "SIGKILL" in end.unconfirmed
    assert f"session {shell}" in end.unconfirmed and "1 process of it" in end.unconfirmed
    assert commandfile.read_command("s1", command.id, h.root) == ended.command
    assert json.loads((h.directory(command) / "end.json").read_text())["unconfirmed"]
    assert any("NOT CONFIRMED DEAD" in remark for remark in end.remarks)
    monkeypatch.undo()
    assert h.runner.close(5.0) == (Unconfirmed(command.id, shell, "sent SIGKILL and not gone"),)


# -- the property: a process can be found from its record -------------------------


def test_the_process_is_on_record_as_soon_as_it_exists_and_can_be_found_by_it(
    h: Harness,
) -> None:
    command = h.start(TREE)
    shell, child = h.pids(command, "shell", "child")
    # Output is not read until the record is written.
    started = commandfile.read_started("s1", command.id, h.root)
    assert started is not None
    assert (started.pid, started.pgid) == (shell, shell)
    stat = Path(f"/proc/{shell}/stat").read_text().rsplit(")", 1)[1].split()
    assert started.start_ticks == int(stat[19])
    assert started.boot_id == Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    found = commandrun.survivor(started)
    assert found == Survivor(pid=shell, pgid=shell, members=2, groups=(shell,))
    assert commandrun.kill_line(found) == f"kill -- -{shell}"

    # A record of another boot, and one whose pid now belongs to a process
    # that started at another moment, are of some other process: gone.
    other_boot = commandrun.survivor(dataclasses.replace(started, boot_id="another"))
    assert other_boot == Gone(commandrun.ANOTHER_BOOT)
    assert started.start_ticks is not None
    # Asked of the record as a build without the tag wrote it: with one, the
    # child is still found, by what it carries.
    reused = commandrun.survivor(
        dataclasses.replace(started, start_ticks=started.start_ticks + 1, tag=None)
    )
    assert reused == Gone(commandrun.NUMBER_REUSED)
    # And one that lacks either fact cannot be matched to anything, which is
    # not the same as nothing being there.
    for lacking in ({"boot_id": None}, {"start_ticks": None}):
        blind = commandrun.survivor(dataclasses.replace(started, **lacking))  # type: ignore[arg-type]
        assert blind == NotLooked(commandrun.NOT_ON_RECORD)

    # The shell killed and not yet reaped: it is still what holds the number,
    # so its child is still known to be this command's.
    os.kill(shell, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while commandrun.survivor(started) == found:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    left = commandrun.survivor(started)
    ended = h.wait(command)
    if not isinstance(left, Gone):
        # Seen between the shell's death and the runner ending what it left.
        assert left == Survivor(pid=shell, pgid=shell, members=1, groups=(shell,))
    assert _outcome(ended) == Exited(-signal.SIGKILL) and _end(ended).leftover == 1
    assert _gone([shell, child], within=0.0)
    assert commandrun.survivor(started) == Gone(commandrun.NOTHING_ALIVE + commandrun.NONE_TAGGED)


def test_the_kill_line_ends_what_the_survivor_names(tmp_path: Path, mark: str) -> None:
    """
    A group nothing supervises, as a pptmstr that died leaves one, found from
    its record and ended by the line the row gives the operator.
    """
    process = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 1000 & sleep 1000 & wait"],
        start_new_session=True,
        env={**os.environ, MARK: mark},
    )
    try:
        started = commandrun.started_now(process.pid, at=5.0)
        deadline = time.monotonic() + 5
        while not isinstance(found := commandrun.survivor(started), Survivor) or found.members < 3:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        line = commandrun.kill_line(found)
        assert subprocess.run(["/bin/sh", "-c", line], check=False).returncode == 0
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while not isinstance(commandrun.survivor(started), Gone):
            assert time.monotonic() < deadline
            time.sleep(0.01)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def test_a_command_that_never_became_a_process_has_no_started_record(h: Harness) -> None:
    command = h.command("echo never")
    assert command.run is not None
    command = dataclasses.replace(
        command, run=dataclasses.replace(command.run, cwd=str(h.cwd / "missing"))
    )
    h.runner.start(command)
    assert isinstance(_outcome(h.wait(command)), NotStarted)
    assert commandfile.read_started("s1", command.id, h.root) is None


# -- the property: an owner that gave up is not left holding the session ----------


def test_an_abandoned_command_is_killed_at_once_and_its_session_is_free(
    h: Harness, stuck_log: None
) -> None:
    command = h.start(DEAF_TREE)
    h.runner.abandon("cmd-20261013T101500Z-ffffffffffff")
    deadline = time.monotonic() + 10
    while (started := commandfile.read_started("s1", command.id, h.root)) is None:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    while not isinstance(found := commandrun.survivor(started), Survivor) or found.members < 3:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    members = commandrun.session_members(started.pid)
    assert members is not None

    h.runner.abandon(command.id)
    h.runner.abandon(command.id)

    # Killed from the caller's thread: no grace, and no tick of the worker's.
    assert _gone(list(members), within=0.3)
    # The slot was free when ``abandon`` returned, whatever the worker is doing.
    assert _outcome(h.run("echo next")) == Exited(0)
    ended = h.wait(command)
    # The owner had declared it ``EndUnknown``, and the file agrees.
    assert _outcome(ended) == EndUnknown()
    assert commandfile.read_command("s1", command.id, h.root) == ended.command
    assert len([e for e in h.ended if e.command.id == command.id]) == 1


def test_a_command_whose_worker_is_held_by_the_disk_is_still_killed_when_abandoned(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    What ``abandon`` is for: the worker is in a write that does not return, so
    nothing it would do on its next tick happens. The kill is the caller's.
    """
    release = threading.Event()
    real = commandfile.write_started
    calls: list[str] = []

    def held(session_id: str, command_id: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(command_id)
        if len(calls) == 1:
            release.wait()
        return real(session_id, command_id, *args, **kwargs)

    monkeypatch.setattr(commandfile, "write_started", held)
    pid_out = h.cwd / "pid"
    try:
        # Something in a group of its own as well: the kill is the session's.
        command = h.start(
            f"trap '' TERM; echo $$ > {pid_out}; timeout 600 sleep 1000 & wait", timeout_s=0.2
        )
        deadline = time.monotonic() + 10
        while not pid_out.exists() or not pid_out.read_text().strip():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        shell = int(pid_out.read_text())
        # Its own timeout has passed, and the worker that keeps it is not looking.
        time.sleep(0.5)
        assert _alive(shell) and h.ended == []
        before = commandrun.session_members(shell)
        assert before is not None and len(before) == 3
        assert len({os.getpgid(pid) for pid in before}) == 2

        assert h.runner.abandon(command.id) is True
        assert _gone([shell], within=1.0)
        members = commandrun.session_members(shell)
        assert members == (), members
        assert _outcome(h.run("echo next")) == Exited(0)
        assert [e.command.id for e in h.ended] != [command.id]
    finally:
        release.set()
    assert _outcome(h.wait(command)) == EndUnknown()
    assert h.runner.abandon(command.id) is False


def test_an_abandoned_command_does_not_take_back_a_slot_given_to_the_next(h: Harness) -> None:
    first = h.start(TREE)
    h.pids(first, "shell", "child")
    h.runner.abandon(first.id)
    second = h.start(TREE)
    h.pids(second, "shell", "child")
    assert _outcome(h.wait(first)) == EndUnknown()
    # The first's end did not free the slot the second holds.
    third = h.run("echo never")
    assert _outcome(third) == NotStarted(commandrun.SESSION_BUSY)
    h.runner.stop(second.id)
    assert _outcome(h.wait(second)) == Cancelled(CancelledBy.OPERATOR)


@pytest.mark.parametrize("tail_bytes", [0, -1, commandfile.MAX_TAIL_FILE_BYTES + 1])
def test_a_tail_that_would_keep_everything_or_nothing_is_refused(tail_bytes: int) -> None:
    with pytest.raises(ValueError, match="tail_bytes"):
        Runner(lambda ended: None, tail_bytes=tail_bytes)
    with pytest.raises(ValueError, match="max_log_bytes"):
        Runner(lambda ended: None, max_log_bytes=-1)


# -- output -----------------------------------------------------------------------


def test_twenty_mebibytes_are_counted_and_eight_are_kept_with_the_tail(h: Harness) -> None:
    produced = 20 << 20
    ended = h.run(f"head -c {produced} /dev/zero | tr '\\0' x; echo; echo THE-END")
    end = _end(ended)
    assert end.outcome == Exited(0)
    assert end.output_bytes == produced + len("\nTHE-END\n")
    directory = h.directory(ended.command)
    assert (directory / "output.log").stat().st_size == commandfile.MAX_LOG_BYTES == 8 << 20
    assert (directory / "output.log").read_bytes() == b"x" * (8 << 20)
    kept = (directory / "output.tail").read_bytes()
    assert len(kept) == commandfile.MAX_TAIL_FILE_BYTES and kept.endswith(b"x\nTHE-END\n")
    assert end.tail.endswith("x\nTHE-END\n") and len(end.tail) == MAX_TAIL_CHARS
    assert commandfile.tail("s1", ended.command.id, h.root) == kept.decode()
    assert commandfile.read_command("s1", ended.command.id, h.root) == ended.command


def test_the_log_is_cut_at_the_cap_exactly_and_the_tail_is_the_last_bytes(
    tmp_path: Path, mark: str
) -> None:
    h = Harness(tmp_path, mark, max_log_bytes=1000, tail_bytes=100)
    ended = h.run("i=0; while [ $i -lt 500 ]; do printf '%04d\\n' $i; i=$((i+1)); done")
    whole = b"".join(b"%04d\n" % n for n in range(500))
    assert _end(ended).output_bytes == len(whole)
    assert h.log(ended.command) == whole[:1000]
    assert (h.directory(ended.command) / "output.tail").read_bytes() == whole[-100:]
    assert _end(ended).tail == whole[-100:].decode()
    assert h.runner.close(5.0) == ()


def test_output_that_fits_the_log_has_no_tail_file(tmp_path: Path, mark: str) -> None:
    h = Harness(tmp_path, mark, max_log_bytes=1000, tail_bytes=100)
    ended = h.run("printf '%01000d' 0")
    assert _end(ended).output_bytes == 1000 and len(h.log(ended.command)) == 1000
    assert not (h.directory(ended.command) / "output.tail").exists()
    assert h.runner.close(5.0) == ()


def test_the_tail_handed_back_is_cut_to_lines_and_characters() -> None:
    lines = "".join(f"{n}\n" for n in range(200))
    assert commandrun.cut_tail(lines.encode()) == "".join(f"{n}\n" for n in range(140, 200))
    assert commandrun.cut_tail(lines.encode()).count("\n") == MAX_TAIL_LINES
    long = ("y" * 9_999 + "\n").encode()
    assert commandrun.cut_tail(long) == "y" * (MAX_TAIL_CHARS - 1) + "\n"
    assert commandrun.cut_tail(b"ok \xff\xe2\x82") == "ok ��"
    assert commandrun.cut_tail(b"") == ""
    assert commandrun.cut_tail(b"no newline") == "no newline"


def test_output_that_is_not_text_ends_a_command_all_the_same(h: Harness) -> None:
    ended = h.run("printf '\\377\\000\\033[2Jx'")
    assert _outcome(ended) == Exited(0)
    assert h.log(ended.command) == b"\xff\x00\x1b[2Jx"
    assert _end(ended).tail == "�\x00\x1b[2Jx"
    assert commandfile.read_command("s1", ended.command.id, h.root) == ended.command


def test_output_is_in_the_log_while_the_command_runs(h: Harness) -> None:
    command = h.start("echo early 1; sleep 1000")
    assert h.pids(command, "early") == [1]
    h.runner.stop(command.id)
    h.wait(command)


# -- the caller's thread ----------------------------------------------------------


def test_start_returns_at_once_and_the_end_comes_on_another_thread(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = commandfile.write_run

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.6)
        return real(*args, **kwargs)

    monkeypatch.setattr(commandfile, "write_run", slow)
    command = h.command("sleep 1000")
    began = time.monotonic()
    h.runner.start(command)
    h.runner.stop("cmd-20261013T101500Z-ffffffffffff")
    assert time.monotonic() - began < 0.3
    assert h.ended == []
    began = time.monotonic()
    h.runner.stop(command.id)
    assert time.monotonic() - began < 0.3
    ended = h.wait(command)
    assert h.threads and threading.get_ident() not in h.threads
    # The stop arrived while the run was still being recorded: nothing started,
    # and the end says it was the stop and not a shutdown.
    assert _outcome(ended) == NotStarted("stopped before it was started")
    assert ended.command.stop_asked_at is not None and not h.argv_out.exists()
    assert commandfile.read_command("s1", command.id, h.root) == ended.command


def test_a_command_with_no_run_or_with_an_end_is_not_taken(h: Harness) -> None:
    command = h.command("echo ran")
    with pytest.raises(ValueError):
        h.runner.start(dataclasses.replace(command, run=None))
    with pytest.raises(ValueError):
        h.runner.start(dataclasses.replace(command, end=End(Exited(0), at=1.0)))
    time.sleep(0.2)
    assert h.ended == [] and not h.argv_out.exists()


# -- one per session, and one end each --------------------------------------------


def test_a_second_command_of_a_session_is_not_started_and_the_first_is_untouched(
    h: Harness,
) -> None:
    first = h.start(TREE)
    pids = h.pids(first, "shell", "child")
    second = h.start("echo second")
    other = h.start("echo other", session="s2")
    refused = h.wait(second)
    assert _outcome(refused) == NotStarted(commandrun.SESSION_BUSY)
    assert _outcome(h.wait(other)) == Exited(0)
    # Its approval is recorded all the same, and so is why nothing came of it.
    assert commandfile.read_command("s1", second.id, h.root) == refused.command
    assert refused.command.run == second.run
    assert all(_alive(pid) for pid in pids)
    # And the refusal did not free the slot the first still holds.
    assert _outcome(h.run("echo also second")) == NotStarted(commandrun.SESSION_BUSY)
    h.runner.stop(first.id)
    assert _outcome(h.wait(first)) == Cancelled(CancelledBy.OPERATOR)
    assert _outcome(h.run("echo third")) == Exited(0)
    assert len(h.ended) == 5


def test_a_command_handed_over_twice_runs_once_and_ends_once(h: Harness) -> None:
    command = h.command("echo once")
    h.runner.start(command)
    h.runner.start(command)
    h.wait(command)
    h.runner.start(command)
    time.sleep(0.3)
    assert [_outcome(e) for e in h.ended] == [Exited(0)]
    assert h.log(command) == b"once\n"


@pytest.mark.parametrize("where", ["before the process", "while it runs", "after it exits"])
def test_a_failure_of_the_runner_itself_is_still_one_end(
    h: Harness, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("broken on purpose")

    name = {
        "before the process": "environment",
        "while it runs": "_exited",
        "after it exits": "cut_tail",
    }
    monkeypatch.setattr(commandrun, name[where], broken)
    command = h.start(OWN_GROUP if where == "while it runs" else "echo ran")
    ended = h.wait(command)
    time.sleep(0.2)
    assert len(h.ended) == 1
    if where == "before the process":
        outcome = _outcome(ended)
        assert isinstance(outcome, NotStarted) and "broken on purpose" in outcome.reason
        assert not h.argv_out.exists()
    else:
        # A process existed, so how it ended is what is not known.
        assert _outcome(ended) == EndUnknown()
        if where == "while it runs":
            # Its session, and not only the shell's group.
            assert _gone(h.pids(command, "shell", "inner"))
    assert commandfile.read_command("s1", command.id, h.root) == ended.command
    monkeypatch.undo()
    assert _outcome(h.run("echo next")) == Exited(0)


def test_a_callback_that_raises_does_not_cost_the_next_command(
    tmp_path: Path, mark: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    raised: list[BaseException | None] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: raised.append(args.exc_value))
    h = Harness(tmp_path, mark)
    seen: list[Ended] = []

    def on_end(ended: Ended) -> None:
        seen.append(ended)
        raise RuntimeError("the callback's own")

    runner = Runner(on_end, root=h.root, environ=h.environ)
    for text in ("echo one", "echo two"):
        runner.start(h.command(text))
        deadline = time.monotonic() + 10
        while len(raised) < len(seen) or not seen or _end(seen[-1]).tail != text[5:] + "\n":
            assert time.monotonic() < deadline
            time.sleep(0.01)
    assert runner.close(5.0) == ()
    assert [_outcome(e) for e in seen] == [Exited(0), Exited(0)] and len(raised) == 2


# -- close ------------------------------------------------------------------------


def test_close_mid_run_ends_each_as_cancelled_by_shutdown(h: Harness) -> None:
    commands = [h.start(TREE, session=f"s{n}") for n in range(4)]
    pids = [pid for command in commands for pid in h.pids(command, "shell", "child")]
    began = time.monotonic()
    assert h.runner.close(10.0) == ()
    assert time.monotonic() - began < 5
    # Every end was delivered before close returned.
    assert sorted(e.command.id for e in h.ended) == sorted(c.id for c in commands)
    assert all(_outcome(e) == Cancelled(CancelledBy.SHUTDOWN) for e in h.ended)
    assert all(e.command.stop_asked_at is None for e in h.ended)
    assert _gone(pids)
    for ended in h.ended:
        assert (
            commandfile.read_command(ended.command.session_id, ended.command.id, h.root)
            == ended.command
        )


def test_after_close_a_command_is_not_started(h: Harness) -> None:
    assert h.runner.close(1.0) == ()
    assert h.runner.close(1.0) == ()
    ended = h.run("echo ran")
    assert _outcome(ended) == NotStarted(commandrun.RUNNER_CLOSED)
    assert not h.argv_out.exists()
    assert commandfile.read_command("s1", ended.command.id, h.root) == ended.command


def test_a_stopped_command_closed_over_is_the_operators_cancel(h: Harness) -> None:
    command = h.start(DEAF_TREE)
    pids = h.pids(command, "shell", "child", "grandchild")
    h.runner.stop(command.id, at=7.0)
    assert h.runner.close(10.0) == ()
    assert [_outcome(e) for e in h.ended] == [Cancelled(CancelledBy.OPERATOR)]
    assert _gone(pids)


def test_close_returns_at_its_bound_and_names_what_it_did_not_see_end(
    tmp_path: Path, mark: str
) -> None:
    h = Harness(tmp_path, mark, grace_s=30.0, drain_s=0.2)
    command = h.start(DEAF_TREE)
    pids = h.pids(command, "shell", "child", "grandchild")
    began = time.monotonic()
    left = h.runner.close(0.0)
    assert time.monotonic() - began < 0.5
    assert left == (Unconfirmed(command.id, pids[0], "its end had not been recorded in time"),)
    assert h.ended == []
    # The bound also shortened the grace: nothing waits thirty seconds for TERM.
    ended = h.wait(command, within=5.0)
    assert _outcome(ended) == Cancelled(CancelledBy.SHUTDOWN)
    assert _gone(pids)
    assert h.runner.close(5.0) == ()


def test_fifty_mixed_commands_closed_mid_run_end_exactly_once_each(
    tmp_path: Path, mark: str
) -> None:
    h = Harness(tmp_path, mark, grace_s=0.3, drain_s=0.2)
    expected: dict[str, tuple[type, ...]] = {}
    commands: list[Command] = []
    for n in range(50):
        kind = n % 7
        session = f"s{n}"
        kinds: tuple[type, ...]
        if kind == 0:
            command, kinds = h.start(f"echo {n}; exit 3", session=session), (Exited,)
        elif kind == 1:
            command, kinds = h.start(TREE, session=session), (Cancelled,)
        elif kind == 2:
            # The session of the command before it, which is still running.
            command, kinds = h.start("echo never", session=f"s{n - 1}"), (NotStarted,)
        elif kind == 3:
            command, kinds = h.start("echo never", session=session, request=False), (NotStarted,)
        elif kind == 4:
            command, kinds = h.start(DEAF_TREE, session=session, timeout_s=0.2), (TimedOut,)
        elif kind == 5:
            command = h.start("sleep 1000", session=session)
            h.runner.stop(command.id)
            kinds = (Cancelled, NotStarted)
        else:
            command, kinds = h.start("sleep 1000 & echo done", session=session), (Exited,)
        commands.append(command)
        expected[command.id] = kinds
    time.sleep(0.4)

    left = h.runner.close(20.0)

    assert left == ()
    counts = {command.id: 0 for command in commands}
    for ended in h.ended:
        counts[ended.command.id] += 1
    assert set(counts.values()) == {1}, {k: v for k, v in counts.items() if v != 1}
    for ended in h.ended:
        outcome = _outcome(ended)
        assert isinstance(outcome, expected[ended.command.id]), (ended.command.asked, outcome)
        on_disk = commandfile.read_command(ended.command.session_id, ended.command.id, h.root)
        if isinstance(ended.record, commandfile.Written) and ended.command.run == getattr(
            on_disk, "run", None
        ):
            assert on_disk == ended.command
    by = {type(_outcome(e)) for e in h.ended}
    assert by == {Exited, Cancelled, NotStarted, TimedOut}
    # The background sleeps of the commands that exited by themselves among them.
    assert _marked(mark) == []
    assert h.runner.close(1.0) == ()
    assert len(h.ended) == 50


# -- which shell, which environment -----------------------------------------------


def _executable(path: Path) -> str:
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def test_the_shell_is_the_environments_when_that_can_be_run(tmp_path: Path) -> None:
    bash = _executable(tmp_path / "bash")
    fish = _executable(tmp_path / "fish")
    assert commandrun.shell_for({"SHELL": bash}, lambda: fish) == Shell(bash, True, "SHELL")
    assert commandrun.shell_for({"SHELL": fish}, lambda: bash) == Shell(fish, False, "SHELL")


@pytest.mark.parametrize("name", ["sh", "bash", "zsh", "dash", "ksh"])
def test_the_shells_that_read_a_command_as_bash_does(tmp_path: Path, name: str) -> None:
    found = commandrun.shell_for({"SHELL": _executable(tmp_path / name)}, lambda: None)
    assert isinstance(found, Shell) and found.posix


@pytest.mark.parametrize("name", ["fish", "nu", "xonsh", "bash.bak", "tcsh"])
def test_a_shell_that_does_not_is_used_and_flagged(tmp_path: Path, name: str) -> None:
    found = commandrun.shell_for({"SHELL": _executable(tmp_path / name)}, lambda: None)
    assert isinstance(found, Shell) and not found.posix


def test_a_shell_variable_that_cannot_be_run_falls_to_the_password_entry(tmp_path: Path) -> None:
    zsh = _executable(tmp_path / "zsh")
    plain = tmp_path / "plain"
    plain.write_text("x")
    (tmp_path / "dir").mkdir()
    for bad in (
        None,
        "",
        "bash",
        "./bash",
        str(plain),
        str(tmp_path / "dir"),
        str(tmp_path / "missing"),
    ):
        environ = {} if bad is None else {"SHELL": bad}
        assert commandrun.shell_for(environ, lambda: zsh) == Shell(zsh, True, "passwd"), bad


def test_with_no_usable_shell_there_is_none_and_the_reason_names_both(tmp_path: Path) -> None:
    found = commandrun.shell_for({"SHELL": "bash"}, lambda: str(tmp_path / "missing"))
    assert isinstance(found, NoShell)
    assert "$SHELL (bash) is not an absolute path" in found.reason
    assert f"({tmp_path / 'missing'}) is not a file" in found.reason
    neither = commandrun.shell_for({}, lambda: None)
    assert isinstance(neither, NoShell) and neither.reason.count("is not set") == 2


def test_the_default_lookup_is_this_users_password_entry() -> None:
    import pwd

    assert commandrun.passwd_shell() == (pwd.getpwuid(os.getuid()).pw_shell or None)
    assert isinstance(commandrun.shell_for(), (Shell, NoShell))


def test_the_environment_is_pptmstrs_without_its_virtualenv() -> None:
    given = {
        "VIRTUAL_ENV": "/opt/p/.venv",
        "PATH": "/opt/p/.venv/bin:/usr/bin:/opt/p/.venv/bin/:/opt/p/.venv/bin2:/bin",
        "PYTHONPATH": "/the/operators",
        "HOME": "/home/op",
        "SHELL": "/bin/zsh",
    }
    before = dict(given)
    assert commandrun.environment(given) == {
        "PATH": "/usr/bin:/opt/p/.venv/bin2:/bin",
        "PYTHONPATH": "/the/operators",
        "HOME": "/home/op",
        "SHELL": "/bin/zsh",
    }
    assert given == before, "the mapping it was given is not changed"
    assert commandrun.environment_changes(given) == (
        "VIRTUAL_ENV (/opt/p/.venv) is unset",
        "/opt/p/.venv/bin is taken off PATH",
        commandrun.TAG_SET,
    )


def test_with_no_virtualenv_the_environment_is_a_copy() -> None:
    given = {"PATH": "/usr/bin:/opt/x/.venv/bin", "HOME": "/home/op"}
    got = commandrun.environment(given)
    assert got == given and got is not given
    assert commandrun.environment_changes(given) == (commandrun.TAG_SET,)


def test_a_virtualenv_that_is_not_on_the_path_is_only_unset() -> None:
    given = {"VIRTUAL_ENV": "/v", "PATH": "/usr/bin"}
    assert commandrun.environment(given) == {"PATH": "/usr/bin"}
    assert commandrun.environment_changes(given) == (
        "VIRTUAL_ENV (/v) is unset",
        commandrun.TAG_SET,
    )
    assert commandrun.environment({"VIRTUAL_ENV": "/v"}) == {"PATH": os.defpath}


def test_what_would_run_a_file_of_the_sessions_directory_is_taken_out() -> None:
    given = {
        "PATH": ":/usr/bin:.:bin:./tools:node_modules/.bin::/bin:",
        "BASH_ENV": ".bashenv",
        "ENV": "/home/op/.shrc",
        "HOME": "/home/op",
    }
    assert commandrun.environment(given) == {"PATH": "/usr/bin:/bin", "HOME": "/home/op"}
    changes = commandrun.environment_changes(given)
    assert len(changes) == 4 and changes[3] == commandrun.TAG_SET
    assert changes[0].startswith("BASH_ENV (.bashenv) is unset")
    assert changes[1].startswith("ENV (/home/op/.shrc) is unset")
    assert changes[2].startswith(
        "7 PATH entries not an absolute path ('', '.', 'bin', './tools', 'node_modules/.bin') "
        "are taken off"
    )
    one = commandrun.environment_changes({"PATH": "/bin:"})
    assert one == (
        "1 PATH entry not an absolute path ('') is taken off: a shell looks each up in "
        "the directory the command runs in",
        commandrun.TAG_SET,
    )
    # A ``PATH`` nothing is left of, and one that was never there, are neither
    # left so: to a shell both are the current directory.
    assert commandrun.environment({"PATH": ""}) == {"PATH": os.defpath}
    assert commandrun.environment({"PATH": ".", "HOME": "/h"}) == {
        "HOME": "/h",
        "PATH": os.defpath,
    }
    assert commandrun.environment_changes({"PATH": ""})[-2] == (
        f"PATH is set to {os.defpath}: no entry of it is left, and a shell with none"
        " searches the current directory"
    )
    assert commandrun.environment({"HOME": "/h"}) == {"HOME": "/h", "PATH": os.defpath}
    assert commandrun.environment_changes({"HOME": "/h"}) == (
        f"PATH is set to {os.defpath}: none was set, and a shell with none searches the"
        " current directory",
        commandrun.TAG_SET,
    )


def test_a_real_bash_does_not_source_a_file_the_environment_named(
    h: Harness, tmp_path: Path
) -> None:
    """
    What ``BASH_ENV`` is taken out for, against the shell that reads it: with
    it passed through, the file runs before the approved command does.
    """
    if not os.access("/bin/bash", os.X_OK):
        pytest.skip("no /bin/bash")
    home = tmp_path / "home"
    home.mkdir()
    (h.cwd / "planted").write_text("echo planted-file-ran\n")
    (h.cwd / "tool").write_text("#!/bin/sh\necho planted-tool-ran\n")
    (h.cwd / "tool").chmod(0o755)
    env = {"HOME": str(home), "BASH_ENV": "planted", "PATH": f":{os.environ['PATH']}"}
    seen = subprocess.run(
        ["/bin/bash", "-c", "tool; echo approved"],
        cwd=h.cwd,
        env={**h.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    assert seen.stdout == "planted-file-ran\nplanted-tool-ran\napproved\n", "the hazard is real"

    h.environ.update(env)
    command = h.command("tool; echo approved")
    assert command.run is not None
    command = dataclasses.replace(command, run=dataclasses.replace(command.run, shell="/bin/bash"))
    h.runner.start(command)
    h.wait(command)
    out = h.log(command).decode()
    assert "planted" not in out and out.endswith("approved\n"), out


def test_the_default_environment_is_this_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", "/v")
    monkeypatch.setenv("PATH", "/v/bin:/usr/bin")
    monkeypatch.setenv("KEPT", "1")
    got = commandrun.environment()
    assert "VIRTUAL_ENV" not in got and got["PATH"] == "/usr/bin" and got["KEPT"] == "1"
    assert len(commandrun.environment_changes()) == 3


def test_a_runner_built_as_the_application_builds_one_reads_the_environment_at_each_start(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``app._start`` gives the runner no ``environ``, and the entry point takes the
    library's variables back out of ``os.environ`` (``pairing.release``) after
    modules are imported. A runner that copied the environment when it was made
    would hand every command the checkout's own pair.
    """
    monkeypatch.setenv(MARK, h.environ[MARK])
    monkeypatch.setenv("ARGV_OUT", h.environ["ARGV_OUT"])
    monkeypatch.setenv("IMTYPESET_PROBE", "the checkout's own")
    assert h.runner.close(10.0) == ()
    h.runner = Runner(h._on_end, root=h.root, grace_s=0.4, drain_s=0.3)
    monkeypatch.delenv("IMTYPESET_PROBE")

    ended = h.run("printf '%s' \"${IMTYPESET_PROBE-unset}\"")
    assert _end(ended).tail == "unset"


# -- the property: nothing the command started in its session is left --------------
#
# The shell's process group is not the command: ``timeout`` and a job-control
# shell give what they start groups of their own. Each test here fails against
# a runner that signals, counts or looks for the shell's group alone.


@pytest.mark.parametrize("how", ["stop", "timeout", "close", "abandon"])
def test_a_process_in_a_group_of_its_own_does_not_outlive_the_commands_end(
    h: Harness, how: str
) -> None:
    command = h.start(OWN_GROUP, timeout_s=0.3 if how == "timeout" else 30)
    shell, inner = h.pids(command, "shell", "inner")
    assert os.getpgid(inner) != shell and os.getsid(inner) == shell, "not the case under test"
    if how == "stop":
        h.runner.stop(command.id)
    elif how == "close":
        assert h.runner.close(10.0) == ()
    elif how == "abandon":
        assert h.runner.abandon(command.id) is True
        # From the caller's thread: dead before any tick of the worker's.
        assert _gone([shell, inner], within=0.5)
    ended = h.wait(command)
    assert type(_outcome(ended)) is {"timeout": TimedOut, "abandon": EndUnknown}.get(how, Cancelled)
    assert _gone([shell, inner], within=0.0), "the end was reported with the session alive"
    assert _end(ended).unconfirmed is None
    assert b"after" not in h.log(command)


def test_a_leftover_in_a_group_of_its_own_is_counted_and_ended(h: Harness) -> None:
    command = h.start(
        "timeout 600 sh -c 'echo inner $$; exec sleep 1000' & echo shell $$; sleep 0.3; echo done"
    )
    shell, inner = h.pids(command, "shell", "inner")
    assert os.getpgid(inner) != shell
    ended = h.wait(command)
    # ``timeout``, and the ``sleep`` it runs.
    assert _outcome(ended) == Exited(0) and _end(ended).leftover == 2
    assert _gone([inner], within=0.0) and _end(ended).unconfirmed is None


# Takes TERM by moving to a process group of its own and carrying on: what the
# signals of the wind-down have to be re-aimed for.
REGROUPS = """
import os, signal, time

def moved(signum, frame):
    os.setpgid(0, 0)

signal.signal(signal.SIGTERM, moved)
print("child", os.getpid(), flush=True)
while True:
    time.sleep(1000)
"""


def test_a_process_that_makes_itself_a_group_when_told_to_stop_is_still_killed(
    h: Harness,
) -> None:
    script = h.cwd / "regroups.py"
    script.write_text(REGROUPS)
    command = h.start(f"echo shell $$; {sys.executable} {script}")
    shell, child = h.pids(command, "shell", "child")
    assert os.getpgid(child) == shell
    h.runner.stop(command.id)
    ended = h.wait(command)
    assert _outcome(ended) == Cancelled(CancelledBy.OPERATOR)
    assert _gone([child], within=0.0) and _end(ended).unconfirmed is None
    # It was there to be killed: the grace was taken, and it is what ran out.
    assert _end(ended).duration_s >= 0.4


def test_the_kill_is_aimed_again_at_whatever_groups_the_session_has_at_each_look(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A process that makes itself a group between the listing and the signal is
    missed by that signal. What cannot be staged with a real one is staged
    here: the session's listing, as the wind-down sees it look after look.
    """

    def process(group: int) -> commandrun._Process:
        return commandrun._Process(pid=41, state="S", group=group, session=40, start_ticks=1)

    # For the TERM, for the look after it, for the first KILL -- and by the
    # look after that it has moved; then for the second KILL, and gone.
    looks = [[process(40)], [process(40)], [process(40)], [process(41)], [process(41)], []]
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(commandrun, "_session", lambda sid: looks.pop(0) if looks else [])
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
    monkeypatch.setattr(commandrun, "REAP_S", 0.5)
    quick = Runner(lambda ended: None, root=h.root, grace_s=0.0)
    job = commandrun._Job(command=h.command("true", request=False), refusal=None)

    assert quick._wind_down(job, 40) is None
    assert sent == [
        (40, signal.SIGTERM),
        (40, signal.SIGKILL),
        (40, signal.SIGKILL),
        (41, signal.SIGKILL),
    ]


def test_no_signal_is_ever_aimed_at_group_zero_or_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ``killpg(0)`` is pptmstr's own group and ``killpg(1)`` everything. A group
    reads as 0 when it is outside this pid namespace.
    """

    def process(pid: int, group: int) -> commandrun._Process:
        return commandrun._Process(pid=pid, state="S", group=group, session=40, start_ticks=1)

    listed = [process(41, 0), process(42, 1), process(43, 43), process(44, 43), process(45, 9)]
    assert commandrun._groups(listed) == (9, 43)
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
    monkeypatch.setattr(commandrun, "_session", lambda sid: listed)
    commandrun._signal_session(40, signal.SIGTERM)
    # The shell's own group as well, which no listing has to show.
    assert sent == [(9, signal.SIGTERM), (40, signal.SIGTERM), (43, signal.SIGTERM)]

    def unreadable(sid: int) -> None:
        raise OSError(errno.EMFILE, "Too many open files")

    sent.clear()
    monkeypatch.setattr(commandrun, "_session", unreadable)
    commandrun._signal_session(40, signal.SIGKILL)
    assert sent == [(40, signal.SIGKILL)]


def test_the_look_line_shows_and_the_kill_line_ends_everything_the_survivor_counts(
    mark: str,
) -> None:
    """
    The two lines the operator is handed, run as they are printed, against a
    session with two process groups in it.
    """
    process = subprocess.Popen(
        ["/bin/sh", "-c", "timeout 600 sleep 1000 & sleep 1000 & wait"],
        start_new_session=True,
        env={**os.environ, MARK: mark},
    )
    try:
        started = commandrun.started_now(process.pid, at=5.0)
        deadline = time.monotonic() + 5
        while not isinstance(found := commandrun.survivor(started), Survivor) or found.members < 4:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        counted = commandrun.session_members(process.pid)
        assert counted is not None and len(counted) == found.members == 4
        assert len(found.groups) == 2 and found.groups[0] == process.pid == found.pgid
        assert commandrun.kill_line(found) == f"kill -- -{found.groups[0]} -{found.groups[1]}"

        look = subprocess.run(
            ["/bin/sh", "-c", commandrun.look_line(found)],
            capture_output=True,
            text=True,
            check=False,
        )
        shown = {int(line.split()[0]) for line in look.stdout.splitlines()[1:]}
        assert shown == set(counted), look.stdout + look.stderr

        kill = subprocess.run(["/bin/sh", "-c", commandrun.kill_line(found)], check=False)
        assert kill.returncode == 0
        process.wait(timeout=5)
        assert _gone(list(counted))
        assert commandrun.survivor(started) == Gone(commandrun.NOTHING_ALIVE)
    finally:
        for pid in _marked(mark):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        process.wait()


def test_a_survivor_nobody_counted_the_groups_of_is_killed_by_its_own_group() -> None:
    assert commandrun.kill_line(Survivor(pid=7, pgid=4242, members=3)) == "kill -- -4242"


@pytest.mark.parametrize("groups", [(0,), (1,), (4242, 0), (-5,)])
def test_a_survivor_cannot_name_a_group_that_is_not_a_commands(groups: tuple[int, ...]) -> None:
    """
    The groups are printed after ``kill --``, where 0 is the operator's own
    shell's group and 1 is every process they may signal.
    """
    with pytest.raises(ValueError):
        Survivor(pid=7, pgid=4242, members=3, groups=groups)


# -- the property: only what is known to be the command is named -------------------


def test_a_session_whose_shell_is_gone_is_reported_and_not_named(mark: str) -> None:
    """
    A leaderless session under the number a record holds. It is what a command
    whose shell died leaves, and it is also what a stranger leaves whose leader
    was given that number later and exited: the record cannot tell, so it is
    neither a survivor nor gone, and what is said of it kills nothing.
    """
    process = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 1000 & echo $!; wait"],
        start_new_session=True,
        stdout=subprocess.PIPE,
        env={**os.environ, MARK: mark},
    )
    assert process.stdout is not None
    child = int(process.stdout.readline())
    try:
        started = commandrun.started_now(process.pid, at=5.0)
        whole = Survivor(pid=process.pid, pgid=process.pid, members=2, groups=(process.pid,))
        assert commandrun.survivor(started) == whole

        # Dead and not reaped, the shell still holds its number: the session
        # is the command's and nobody else's.
        os.kill(process.pid, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while commandrun.survivor(started) == whole:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert commandrun.survivor(started) == dataclasses.replace(whole, members=1)

        # Reaped, the number is free to be anybody's, and nothing is named.
        process.wait()
        found = commandrun.survivor(started)
        assert found == Unidentified(session=process.pid, members=1)
        said = commandrun.unidentified_line(found)
        assert (
            f"ps -o pid,pgid,lstart,args -s {process.pid}" in said and "1 process is alive" in said
        )
        assert "kill --" not in said and "pkill" not in said

        # A record that would match any later holder of the number by its
        # start time says no more than this one does.
        older = dataclasses.replace(started, start_ticks=1)
        assert commandrun.survivor(older) == found
    finally:
        process.stdout.close()
        os.kill(child, signal.SIGKILL)
        if process.poll() is None:
            process.kill()
            process.wait()
    assert _gone([child])
    assert commandrun.survivor(started) == Gone(commandrun.NOTHING_ALIVE)


# -- the property: not known is not gone -------------------------------------------

_BOOT = "0b0b0b0b-0000-4000-8000-000000000000"


def _fake_proc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    processes: dict[int, tuple[str, int, int, int]],
) -> Path:
    """
    A ``/proc`` of this test's making: each pid with its state, group, session
    and start time. What a real one cannot be made to hold on demand -- a pid
    in other hands, a file that cannot be read -- is put here.
    """
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self" / "stat").write_text("1 (self) S\n")
    (proc / "sys" / "kernel" / "random").mkdir(parents=True)
    (proc / "sys" / "kernel" / "random" / "boot_id").write_text(_BOOT + "\n")
    for pid, (state, group, session, ticks) in processes.items():
        (proc / str(pid)).mkdir()
        rest = " ".join(["0"] * 15)
        (proc / str(pid) / "stat").write_text(
            f"{pid} (a name) with) spaces) {state} 1 {group} {session} {rest} {ticks} 0 0\n"
        )
    monkeypatch.setattr(commandrun, "_PROC", str(proc))
    return proc


def _recorded(pid: int, ticks: int) -> Any:
    from pptmstr.command import Started

    return Started(pid=pid, pgid=pid, at=1.0, start_ticks=ticks, boot_id=_BOOT)


def test_a_look_reads_the_session_and_tells_the_shell_by_its_start_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processes = {
        50: ("S", 50, 50, 700),
        51: ("S", 51, 50, 710),
        52: ("R", 51, 50, 711),
        53: ("Z", 50, 50, 712),
        60: ("S", 60, 60, 713),
        61: ("S", 50, 60, 714),
    }
    _fake_proc(tmp_path, monkeypatch, processes)
    # By session: 61 has the group's number and is another session's; the
    # zombie is not alive.
    assert commandrun.session_members(50) == (50, 51, 52) or set(
        commandrun.session_members(50) or ()
    ) == {50, 51, 52}
    assert commandrun.survivor(_recorded(50, 700)) == Survivor(
        pid=50, pgid=50, members=3, groups=(50, 51)
    )
    # The pid in other hands: whatever is in a session of that number now, the
    # one the record is of emptied first.
    assert commandrun.survivor(_recorded(50, 699)) == Gone(commandrun.NUMBER_REUSED)
    assert commandrun.survivor(dataclasses.replace(_recorded(50, 700), boot_id="x")) == Gone(
        commandrun.ANOTHER_BOOT
    )
    assert commandrun.survivor(_recorded(70, 700)) == Gone(commandrun.NOTHING_ALIVE)


def test_a_shell_that_is_a_zombie_still_identifies_what_is_in_its_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_proc(tmp_path, monkeypatch, {50: ("Z", 50, 50, 700), 51: ("S", 51, 50, 710)})
    assert commandrun.survivor(_recorded(50, 700)) == Survivor(
        pid=50, pgid=50, members=1, groups=(51,)
    )
    assert commandrun.kill_line(Survivor(pid=50, pgid=50, members=1, groups=(51,))) == (
        "kill -- -51"
    )
    # Alone, it is nothing alive.
    _fake_proc(tmp_path / "second", monkeypatch, {50: ("Z", 50, 50, 700)})
    assert commandrun.survivor(_recorded(50, 700)) == Gone(commandrun.NOTHING_ALIVE)


@pytest.mark.parametrize(
    "spoil",
    ["no /proc", "listing fails", "a stat cannot be opened", "a stat is not one", "no boot id"],
)
def test_a_proc_that_cannot_be_read_is_not_looked_and_never_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spoil: str
) -> None:
    """
    Each of these read as "nothing is alive" would end the row of a command
    that is running and free its session.
    """
    proc = _fake_proc(tmp_path, monkeypatch, {50: ("S", 50, 50, 700), 51: ("S", 51, 50, 710)})
    record = _recorded(50, 700)
    assert isinstance(commandrun.survivor(record), Survivor)
    if spoil == "no /proc":
        (proc / "self" / "stat").unlink()
    elif spoil == "listing fails":
        real = os.listdir

        def failing(path: Any = ".") -> list[str]:
            if str(path) == str(proc):
                raise OSError(errno.EMFILE, "Too many open files")
            return real(path)

        monkeypatch.setattr(os, "listdir", failing)
    elif spoil == "a stat cannot be opened":
        # Not ``ENOENT``: the process may well be there.
        (proc / "51" / "stat").unlink()
        (proc / "51" / "stat").mkdir()
    elif spoil == "a stat is not one":
        (proc / "51" / "stat").write_text("51 (x) S 1\n")
    else:
        (proc / "sys" / "kernel" / "random" / "boot_id").unlink()

    found = commandrun.survivor(record)
    assert isinstance(found, NotLooked), found
    if spoil == "no /proc":
        assert found == NotLooked(commandrun.NO_PROC)
    if spoil == "listing fails":
        assert "Too many open files" in found.reason
    if spoil != "no boot id":
        assert commandrun.session_members(50) is None


def test_a_process_that_goes_while_proc_is_read_is_only_one_fewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _fake_proc(tmp_path, monkeypatch, {50: ("S", 50, 50, 700), 51: ("S", 51, 50, 710)})
    (proc / "51" / "stat").unlink()
    assert commandrun.session_members(50) == (50,)
    assert commandrun.survivor(_recorded(50, 700)) == Survivor(
        pid=50, pgid=50, members=1, groups=(50,)
    )


# -- the property: a command that was given up before it started is not started ----


@pytest.mark.parametrize("asked", ["abandon", "stop", "close"])
def test_a_worker_held_while_making_the_log_starts_nothing_once_asked_to_end(
    h: Harness, monkeypatch: pytest.MonkeyPatch, asked: str
) -> None:
    """
    The log is made on a disk, which can take any time. What was asked for
    while it did is asked about again before the process, and not after.
    """
    entered, release = threading.Event(), threading.Event()
    real = commandfile.open_log

    def held(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        release.wait()
        return real(*args, **kwargs)

    monkeypatch.setattr(commandfile, "open_log", held)
    command = h.start("echo ran")
    assert entered.wait(5)
    if asked == "abandon":
        assert h.runner.abandon(command.id) is True
    elif asked == "stop":
        h.runner.stop(command.id, at=7.0)
    else:
        closing = threading.Thread(target=h.runner.close, args=(5.0,))
        closing.start()
        time.sleep(0.2)
    release.set()
    ended = h.wait(command)
    if asked == "close":
        closing.join()
    assert not h.argv_out.exists(), f"a process was started all the same: {_outcome(ended)}"
    why = {
        "abandon": "pptmstr had stopped waiting for it before it was started",
        "stop": "stopped before it was started",
        "close": commandrun.RUNNER_CLOSED,
    }[asked]
    # No process existed, and the record says that and not "end unknown".
    assert _outcome(ended) == NotStarted(why)
    assert ended.started is None and ended.taken_out == ()
    assert commandfile.read_command("s1", command.id, h.root) == ended.command
    assert commandfile.read_started("s1", command.id, h.root) is None


def test_an_abandon_that_arrives_while_the_process_is_being_made_kills_it_at_once(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The window nothing can close: ``Popen`` has been called and has not
    returned. The abandon finds no process; the worker, on getting one, finds
    the abandon. Its next step is held here, so the kill is not a later tick's.
    """
    real_popen = subprocess.Popen
    release = threading.Event()
    real_started = commandfile.write_started
    made: list[int] = []

    def popen(*args: Any, **kwargs: Any) -> Any:
        process = real_popen(*args, **kwargs)
        made.append(process.pid)
        assert h.runner.abandon(command.id) is True
        return process

    def held(*args: Any, **kwargs: Any) -> Any:
        release.wait()
        return real_started(*args, **kwargs)

    command = h.command(OWN_GROUP)
    monkeypatch.setattr(commandrun.subprocess, "Popen", popen)
    monkeypatch.setattr(commandfile, "write_started", held)
    try:
        h.runner.start(command)
        deadline = time.monotonic() + 5
        while not made:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert _gone(made, within=2.0), "it ran on, with the worker held and its owner gone"
        assert h.ended == []
    finally:
        release.set()
    monkeypatch.undo()
    assert _outcome(h.wait(command)) == EndUnknown()
    assert commandrun.session_members(made[0]) in ((), None)


def test_a_process_record_that_could_not_be_written_is_handed_over_with_the_end(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    ended = h.run("echo ran")
    assert isinstance(ended.started, commandfile.Written)
    assert ended.started.path.name == commandfile.STARTED_NAME

    refused = commandfile.Unwritten("the disk is full")
    monkeypatch.setattr(commandfile, "write_started", lambda *args, **kwargs: refused)
    ended = h.run("echo ran", session="s2")
    assert _outcome(ended) == Exited(0) and ended.started == refused


# -- the property: what the session can write is not searched ----------------------


def test_a_search_path_entry_inside_the_commands_directory_is_taken_out(tmp_path: Path) -> None:
    cwd = tmp_path / "work"
    (cwd / "node_modules" / ".bin").mkdir(parents=True)
    (tmp_path / "work2" / "bin").mkdir(parents=True)
    (tmp_path / "linked").symlink_to(cwd / "node_modules")
    (cwd / "out").symlink_to(tmp_path / "work2")
    given = {
        "PATH": os.pathsep.join(
            [
                f"{cwd}/node_modules/.bin",
                "/usr/bin",
                str(cwd),
                f"{tmp_path}/linked/.bin",
                # Beside it, with its name as a prefix, and reached through it
                # by a link that leads out: neither is inside.
                f"{tmp_path}/work2/bin",
                f"{cwd}/out/bin",
                f"{cwd}/../work/later",
                "/bin",
            ]
        ),
        "LD_LIBRARY_PATH": f"/opt/lib:{cwd}/lib:",
        "PYTHONPATH": f"{cwd}/src",
        "NODE_PATH": "lib/node:/usr/lib/node",
        "PERL5LIB": "/usr/share/perl5",
        "HOME": "/home/op",
        "LD_PRELOAD": f"{cwd}/x.so",
    }
    before = dict(given)
    got = commandrun.environment(given, str(cwd))
    assert got == {
        "PATH": f"/usr/bin:{tmp_path}/work2/bin:{cwd}/out/bin:/bin",
        "LD_LIBRARY_PATH": "/opt/lib",
        "NODE_PATH": "/usr/lib/node",
        "PERL5LIB": "/usr/share/perl5",
        "HOME": "/home/op",
        # The operator's, and nothing but the operator sets it.
        "LD_PRELOAD": f"{cwd}/x.so",
    }
    assert given == before
    lines = commandrun.environment_changes(given, str(cwd))
    assert lines == (
        f"4 PATH entries inside the directory the command runs in ('{cwd}/node_modules/.bin',"
        f" '{cwd}', '{tmp_path}/linked/.bin', '{cwd}/../work/later') are taken off: this"
        " session writes the files there",
        "1 LD_LIBRARY_PATH entry not an absolute path ('') is taken off: a program looks each"
        " up in the directory the command runs in",
        f"1 LD_LIBRARY_PATH entry inside the directory the command runs in ('{cwd}/lib') is"
        " taken off: this session writes the files there",
        f"1 PYTHONPATH entry inside the directory the command runs in ('{cwd}/src') is taken"
        " off: this session writes the files there",
        "PYTHONPATH is unset: no entry of it is left",
        "1 NODE_PATH entry not an absolute path ('lib/node') is taken off: a program looks"
        " each up in the directory the command runs in",
        commandrun.TAG_SET,
    )
    # With no directory named, only what is wrong in every directory goes.
    assert commandrun.environment(given)["PATH"] == given["PATH"]
    assert commandrun.environment(given)["PYTHONPATH"] == f"{cwd}/src"
    assert len(commandrun.environment_changes(given)) == 3
    # The directory itself reached through a link.
    (tmp_path / "alias").symlink_to(cwd)
    assert commandrun.environment({"PATH": f"{cwd}/bin:/bin"}, str(tmp_path / "alias")) == {
        "PATH": "/bin"
    }


def test_a_program_the_session_put_on_the_path_is_not_what_the_command_runs(h: Harness) -> None:
    planted = h.cwd / "node_modules" / ".bin"
    planted.mkdir(parents=True)
    (planted / "deploy").write_text("#!/bin/sh\necho planted-ran\n")
    (planted / "deploy").chmod(0o755)
    h.environ["PATH"] = f"{planted}{os.pathsep}{os.environ['PATH']}"
    seen = subprocess.run(
        [str(h.shell), "-l", "-c", "deploy"],
        cwd=h.cwd,
        env=h.environ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert seen.stdout == "planted-ran\n", "the hazard is real"
    h.argv_out.unlink()

    ended = h.run("deploy; echo approved")
    out = h.log(ended.command).decode()
    assert "planted-ran" not in out and out.endswith("approved\n"), out
    assert (
        f"1 PATH entry inside the directory the command runs in ('{planted}') is taken off:"
        " this session writes the files there"
    ) in ended.taken_out


def test_a_real_bash_reads_an_empty_path_as_the_current_directory(
    h: Harness, tmp_path: Path
) -> None:
    """
    Why a ``PATH`` with nothing left is neither left empty nor unset.
    """
    if not os.access("/bin/bash", os.X_OK):
        pytest.skip("no /bin/bash")
    (h.cwd / "tool").write_text("#!/bin/sh\necho planted-tool-ran\n")
    (h.cwd / "tool").chmod(0o755)
    for hazard in ({"PATH": ""}, {}):
        seen = subprocess.run(
            ["/bin/bash", "-c", "tool"],
            cwd=h.cwd,
            env=hazard,
            capture_output=True,
            text=True,
            check=False,
        )
        assert seen.stdout == "planted-tool-ran\n", f"the hazard is real: {hazard}"
    for given in ({"PATH": "."}, {"PATH": ""}, {}):
        kept = subprocess.run(
            ["/bin/bash", "-c", "tool"],
            cwd=h.cwd,
            env=commandrun.environment(given, str(h.cwd)),
            capture_output=True,
            text=True,
            check=False,
        )
        assert kept.stdout == "" and kept.returncode == 127, given


# -- the property: what is counted as logged is in the log -------------------------


class _ShortLog(io.FileIO):
    """An ``output.log`` whose every write takes at most ``takes`` bytes."""

    takes = 3

    def write(self, data: Any) -> int:
        return super().write(bytes(data)[: self.takes])


@pytest.mark.parametrize("takes", [3, 0])
def test_a_log_write_that_takes_less_than_it_was_given_loses_nothing(
    h: Harness, monkeypatch: pytest.MonkeyPatch, takes: int
) -> None:
    real = commandfile.open_log

    def open_log(*args: Any, **kwargs: Any) -> Any:
        log = real(*args, **kwargs)
        assert isinstance(log, io.FileIO)
        fd = os.dup(log.fileno())
        log.close()
        return _ShortLog(fd, "wb")

    monkeypatch.setattr(_ShortLog, "takes", takes)
    monkeypatch.setattr(commandfile, "open_log", open_log)
    ended = h.run("printf 0123456789")
    assert _end(ended).output_bytes == 10 and _end(ended).tail == "0123456789"
    files = sorted(path.name for path in h.directory(ended.command).iterdir())
    if takes:
        # Asked again for the rest, until it is all there.
        assert h.log(ended.command) == b"0123456789" and "output.tail" not in files
    else:
        # A log that takes nothing holds nothing, and what it does not hold
        # is kept beside it.
        assert h.log(ended.command) == b""
        assert (h.directory(ended.command) / "output.tail").read_bytes() == b"0123456789"


# -- the property: a command's processes carry its id, and are known by it ---------
#
# The session says whose a process is only while the shell is there. Past that
# -- the shell dead, or the process in a session of its own -- what is left is
# the one variable the shell was started with.


# The value each command of ``_tagged_shell`` was given, by its id: what the
# runner holds in a frame and a test has to hold somewhere.
_VALUES: dict[str, str] = {}


def _environ_of(pid: int) -> list[bytes]:
    return Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")


def _execed_environ_of(pid: int) -> list[bytes]:
    """
    A just-started process's environment, once it has one: ``Popen`` returns as
    the child begins to exec, and until the exec has set up the new image the
    process reads as having no environment at all.
    """
    deadline = time.monotonic() + 5
    while (environ := _environ_of(pid)) == [b""]:
        assert time.monotonic() < deadline, f"{pid} still reads as having no environment"
        time.sleep(0.01)
    return environ


def _tagged_shell(script: str, mark: str, command_id: str) -> tuple[subprocess.Popen[bytes], Any]:
    """
    A shell as the runner starts one -- its own session, the command's tag in
    its environment -- that this test holds and may kill, and its record.
    """
    value = _VALUES.setdefault(command_id, commandrun.mint_tag())
    process = subprocess.Popen(
        ["/bin/sh", "-c", script],
        start_new_session=True,
        stdout=subprocess.PIPE,
        env={**os.environ, MARK: mark, commandrun.COMMAND_TAG: value},
    )
    return process, commandrun.started_now(process.pid, at=5.0, tag=commandrun.tag_digest(value))


def _printed(process: subprocess.Popen[bytes], *names: str) -> list[int]:
    assert process.stdout is not None
    words: dict[str, int] = {}
    while not all(name in words for name in names):
        name, pid = process.stdout.readline().split()
        words[name.decode()] = int(pid)
    return [words[name] for name in names]


def _a_command_id() -> str:
    return f"cmd-20261013T101500Z-{uuid.uuid4().hex[:12]}"


def _end_all(process: subprocess.Popen[bytes], pids: list[int]) -> None:
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    if process.poll() is None:
        process.kill()
    process.wait()
    if process.stdout is not None:
        process.stdout.close()
    assert _gone(pids)


def _value_file(h: Harness) -> Path:
    return h.cwd / "the-value"


# Put in front of a command, it has the command's own shell write the value
# it was given to a file of the test's: the one way a test learns it, since
# the runner keeps it in a frame and takes it out of the output.
_TELL = 'printf %s "$PPTMSTR_COMMAND" > the-value; '


def _told(h: Harness, within: float = 10.0) -> str:
    deadline = time.monotonic() + within
    while True:
        with contextlib.suppress(OSError):
            value = _value_file(h).read_text()
            if len(value) == 64:
                return value
        assert time.monotonic() < deadline, "the command did not write its value"
        time.sleep(0.01)


def test_the_tag_reaches_a_grandchild_and_replaces_one_pptmstr_was_started_with(
    h: Harness,
) -> None:
    """
    A pptmstr started from another pptmstr's command has that command's value
    in its own environment. Passed on, this command's processes would be found
    as the other's. What it was is not said on the line that says it was set:
    that line is drawn, and logged.
    """
    outer = "f" * 64
    h.environ[commandrun.COMMAND_TAG] = outer
    command = h.start(_TELL + "echo shell $$; sh -c 'sh -c \"echo grand \\$\\$; sleep 1000\"'")
    shell, grand = h.pids(command, "shell", "grand")
    assert grand != shell
    value = _told(h)
    assert value != outer and value != command.id
    for pid in (shell, grand):
        carried = [e for e in _environ_of(pid) if e.startswith(b"PPTMSTR_COMMAND=")]
        assert carried == [f"PPTMSTR_COMMAND={value}".encode()]
    started = commandfile.read_started("s1", command.id, h.root)
    assert started is not None and started.tag == commandrun.tag_digest(value)
    h.runner.stop(command.id)
    ended = h.wait(command)
    assert ended.taken_out[-1] == commandrun.TAG_OVERWRITTEN
    assert outer not in "".join(ended.taken_out)


def test_the_environment_carries_the_value_it_is_given_and_never_says_what_was_there() -> None:
    given = {"PATH": "/usr/bin", "PPTMSTR_COMMAND": "an outer command's"}
    assert commandrun.environment(given, tag="made for this run") == {
        "PATH": "/usr/bin",
        "PPTMSTR_COMMAND": "made for this run",
    }
    # With no value to give, an inherited one is not passed on either.
    assert commandrun.environment(given) == {"PATH": "/usr/bin"}
    assert commandrun.environment_changes(given) == (
        "PPTMSTR_COMMAND, which was already set, is overwritten with a value made for this"
        " run, which pptmstr shows and records nowhere: everything the command starts"
        " inherits it, and it is how pptmstr tells what the command left running",
    )
    assert "an outer command's" not in "".join(commandrun.environment_changes(given))
    assert commandrun.environment_changes({"PATH": "/usr/bin"}) == (commandrun.TAG_SET,)
    assert commandrun.TAG_SET.startswith("PPTMSTR_COMMAND is set, which you did not set")
    assert "id" not in commandrun.TAG_SET.split(":")[0].split()


def test_a_value_is_made_for_each_run_and_a_record_holds_only_its_digest() -> None:
    import hashlib

    from pptmstr.command import is_tag_digest

    first, second = commandrun.mint_tag(), commandrun.mint_tag()
    assert first != second and len(first) == 64 == len(commandrun.WITHHELD)
    assert set(first) <= set("0123456789abcdef")
    digest = commandrun.tag_digest(first)
    assert digest == hashlib.sha256(first.encode()).hexdigest() and is_tag_digest(digest)
    assert digest != first and commandrun.tag_digest(first.encode()) == digest


def test_what_a_shell_is_given_is_what_was_minted_and_minting_is_the_systems_randomness(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A value that could be worked out from the command -- its id, a digest of
    its id -- would be a secret in name only: every input to it is in the
    command's directory.
    """
    import secrets

    asked: list[int] = []
    real = secrets.token_hex

    def source(nbytes: int | None = None) -> str:
        assert nbytes is not None
        asked.append(nbytes)
        return real(nbytes)

    monkeypatch.setattr(secrets, "token_hex", source)
    assert len(commandrun.mint_tag()) == 64 and asked == [32]
    monkeypatch.setattr(commandrun, "mint_tag", lambda: "5e" * 32)
    ended = h.run(_TELL + "true")
    assert _told(h) == "5e" * 32
    started = commandfile.read_started("s1", ended.command.id, h.root)
    assert started is not None and started.tag == commandrun.tag_digest("5e" * 32)


def _everything_under(root: Path) -> bytes:
    held = []
    for directory, _dirs, files in os.walk(root):
        held.append(directory.encode())
        for name in files:
            held.append(name.encode() + b"\0" + (Path(directory) / name).read_bytes())
    return b"\0".join(held)


def test_nothing_pptmstr_writes_or_hands_over_holds_the_value_of_a_tag(h: Harness) -> None:
    """
    The hazard: a contained session reads a command's directory, and with what
    it finds there starts a process pptmstr takes for that command's. So the
    value is looked for in every byte under the sessions root and in the end
    the callback is given, after a command that prints it three ways -- by
    name, in a listing of its environment, and in a trace.
    """
    ended = h.run(
        _TELL + 'echo "by name $PPTMSTR_COMMAND"; env | grep PPTMSTR_COMMAND; '
        'set -x; : "$PPTMSTR_COMMAND"; set +x; echo done'
    )
    value = _told(h).encode()
    end = ended.command.end
    assert end is not None and end.outcome == Exited(0)
    on_disk = _everything_under(h.root)
    assert value not in on_disk and value.decode() not in repr(ended)
    assert commandrun.tag_digest(value).encode() in on_disk
    log = h.log(ended.command)
    assert log.count(commandrun.WITHHELD) == 3 and log.endswith(b"done\n")
    assert b"by name " + commandrun.WITHHELD + b"\n" in log
    assert b"PPTMSTR_COMMAND=" + commandrun.WITHHELD + b"\n" in log
    assert end.tail.count(commandrun.WITHHELD.decode()) == 3
    # The mark is as long as what it stands for: the count is of what was written.
    assert end.output_bytes == len(log)


def test_a_value_written_in_two_pieces_is_still_taken_out_and_a_part_of_one_is_passed(
    h: Harness,
) -> None:
    whole = h.run(
        _TELL + 'printf "a %s" "${PPTMSTR_COMMAND%????}"; sleep 0.4; '
        'printf "%s z\\n" "${PPTMSTR_COMMAND#${PPTMSTR_COMMAND%????}}"'
    )
    value = _told(h)
    assert h.log(whole.command) == b"a " + commandrun.WITHHELD + b" z\n"
    assert whole.command.end is not None
    assert whole.command.end.tail == "a " + commandrun.WITHHELD.decode() + " z\n"
    _value_file(h).unlink()
    # Kept back while it might be the start of the value, and given out at the
    # end when it was not: nothing a command wrote is lost to the looking.
    part = h.run(_TELL + 'printf "a %s" "${PPTMSTR_COMMAND%????}"')
    assert h.log(part.command) == b"a " + _told(h)[:60].encode()
    assert part.command.end is not None and part.command.end.output_bytes == 62
    assert value != _told(h)


def test_the_value_is_taken_out_wherever_the_pieces_fall() -> None:
    secret = commandrun.mint_tag().encode()
    mark = commandrun.WITHHELD
    data = secret[:10] + secret + b"\n" + secret[:63] + b"x" + secret + secret + secret[:5]
    want = data.replace(secret, mark)
    assert want.count(mark) == 3 and secret not in want and len(want) == len(data)
    for cut in range(len(data) + 1):
        taker = commandrun._Withheld(secret)
        got = taker.feed(data[:cut]) + taker.feed(data[cut:]) + taker.rest()
        assert got == want, cut
    taker = commandrun._Withheld(secret)
    assert b"".join(taker.feed(bytes([b])) for b in data) + taker.rest() == want
    # No more is kept back than could be the start of the value.
    taker = commandrun._Withheld(secret)
    assert taker.feed(b"plain text\n") == b"plain text\n"
    assert taker.feed(b"tail " + secret[:7]) == b"tail " and taker.rest() == secret[:7]
    with pytest.raises(ValueError):
        commandrun._Withheld(b"short")


def test_a_process_started_with_what_the_records_hold_is_not_taken_for_the_command(
    mark: str,
) -> None:
    """
    The hazard itself. Another session's agent can list this command's
    directory, so it knows the command's id and can read ``started.json``. It
    starts processes after the command's shell carrying each thing it could
    have read there as the tag: the id, the digest on the record, and the
    entry a build before this one recorded. None is the command's -- not at
    its end, and not when its shell is gone and a look would name a survivor.
    """
    command_id = _a_command_id()
    process, started = _tagged_shell("echo up $$; exec sleep 1000", mark, command_id)
    _printed(process, "up")
    known = (command_id, started.tag, f"PPTMSTR_COMMAND={command_id}")
    forged = [
        subprocess.Popen(
            ["sleep", "1000"],
            start_new_session=True,
            env={**os.environ, MARK: mark, commandrun.COMMAND_TAG: value},
        )
        for value in known
    ]
    try:
        for each, value in zip(forged, known, strict=True):
            assert f"PPTMSTR_COMMAND={value}".encode() in _execed_environ_of(each.pid)
        assert commandrun.escaped(started) == ()
        found = commandrun.survivor(started)
        assert isinstance(found, Survivor) and found.escaped == () and found.shell
        process.kill()
        process.wait()
        assert commandrun.survivor(started) == Gone(
            commandrun.NOTHING_ALIVE + commandrun.NONE_TAGGED
        )
        # And the value does make one: the look is not blind.
        real = subprocess.Popen(
            ["sleep", "1000"],
            start_new_session=True,
            env={**os.environ, MARK: mark, commandrun.COMMAND_TAG: _VALUES[command_id]},
        )
        forged.append(real)
        _execed_environ_of(real.pid)
        assert commandrun.escaped(started) == (real.pid,)
    finally:
        for each in forged:
            each.kill()
            each.wait()
        _end_all(process, [])


_UNREADABLE = """
import ctypes, os, sys, time
r, w = os.pipe()
pid = os.fork()
if pid == 0:
    os.setsid()
    assert ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) == 0  # PR_SET_DUMPABLE
    for fd in (0, 1, 2, r):
        os.close(fd)
    os.write(w, b"x")
    os.close(w)
    time.sleep(1000)
os.close(w)
os.read(r, 1)
print("out", pid, flush=True)
"""


def _kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while _state(pid) not in (None, "Z") and time.monotonic() < deadline:
        time.sleep(0.01)


def test_a_process_that_left_and_cannot_be_read_is_never_gone_while_its_group_ties_it(
    mark: str, tmp_path: Path
) -> None:
    """
    ``eval $(ssh-agent)``: a process of the same user that left the session
    with ``setsid`` and made itself undumpable. Its environment is refused, so
    nothing says it is the command's; it started after the shell in the
    shell's control group, so nothing says it is not. One look must not write
    it off for ever.
    """
    script = tmp_path / "unreadable.py"
    script.write_text(_UNREADABLE)
    process, started = _tagged_shell(f"{sys.executable} {script}", mark, _a_command_id())
    (out,) = _printed(process, "out")
    try:
        process.wait(timeout=5)
        with pytest.raises(PermissionError):
            _environ_of(out)
        assert started.cgroup is not None
        assert started.cgroup == Path(f"/proc/{out}/cgroup").read_text().strip()
        found = commandrun.survivor(started)
        assert isinstance(found, Unidentified), found
        assert (found.session, found.members, found.unread, found.looked) == (
            process.pid,
            0,
            0,
            True,
        )
        assert out in found.outside
        said = commandrun.unidentified_line(found)
        assert f"pid {process.pid}) is gone" in said and str(out) in said
        assert "kill" not in said.replace("names nothing to kill", "")
        apart = commandrun.outside(started)
        assert apart is not None and apart[0] == () and out in apart[1]
        # What ties it is the group and nothing else: a record without one,
        # or of another, has nothing to count it by, and says so in its words.
        for untied in (None, "0::/some/other/group"):
            blind = commandrun.survivor(dataclasses.replace(started, cgroup=untied))
            assert blind == Gone(commandrun.NOTHING_ALIVE + commandrun.NONE_TAGGED)
        assert "made itself unreadable, as ssh-agent does" in commandrun.NONE_TAGGED
        assert "left the control group" in commandrun.NONE_TAGGED
    finally:
        _kill(out)
        _end_all(process, [])
    assert commandrun.survivor(started) == Gone(commandrun.NOTHING_ALIVE + commandrun.NONE_TAGGED)


def test_the_end_of_a_run_counts_what_it_could_not_read_beside_it(h: Harness) -> None:
    script = h.cwd / "unreadable.py"
    script.write_text(_UNREADABLE)
    ended = h.run(f"{sys.executable} {script}")
    end = ended.command.end
    assert end is not None and end.outcome == Exited(0)
    (out,) = [int(line.split()[1]) for line in h.log(ended.command).decode().splitlines()]
    try:
        assert end.escaped == () and out in end.unreadable
        (remark,) = [said for said in end.remarks if "could not be read" in said]
        assert f"pid {out}" in remark and "may be its" in remark and "kill" not in remark
        reread = commandfile.read_command("s1", ended.command.id, h.root)
        assert isinstance(reread, Command) and reread.end == end
    finally:
        _kill(out)


def test_a_shell_killed_with_its_children_alive_is_a_survivor_and_its_kill_line_ends_exactly_them(
    mark: str,
) -> None:
    """
    The case the session alone cannot answer: the shell is gone, its number is
    free to be anybody's, and what it started is running.
    """
    stranger = subprocess.Popen(["sleep", "1000"], start_new_session=True, env={MARK: mark})
    process, started = _tagged_shell(
        "sleep 1000 & echo a $!; timeout 600 sleep 1000 & echo b $!; wait", mark, _a_command_id()
    )
    a, b = _printed(process, "a", "b")
    try:
        deadline = time.monotonic() + 5
        while len(commandrun.session_members(process.pid) or ()) != 4:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        (b_child,) = set(commandrun.session_members(process.pid) or ()) - {process.pid, a, b}
        os.kill(process.pid, signal.SIGKILL)
        process.wait()

        found = commandrun.survivor(started)
        assert found == Survivor(
            pid=process.pid,
            pgid=process.pid,
            members=3,
            # The shell's own group, which ``a`` is still in, and ``timeout``'s.
            groups=tuple(sorted((process.pid, b))),
            marked=tuple(sorted((a, b, b_child))),
            shell=False,
        )
        line = commandrun.kill_line(found)
        assert line == "kill -- " + " ".join(f"-{g}" for g in sorted((process.pid, b)))
        look = subprocess.run(
            ["/bin/sh", "-c", commandrun.look_line(found)], capture_output=True, text=True
        )
        assert {int(row.split()[0]) for row in look.stdout.splitlines()[1:]} == {a, b, b_child}

        # The same record as a build without the tag wrote it names nothing.
        assert commandrun.survivor(dataclasses.replace(started, tag=None)) == Unidentified(
            session=process.pid, members=3
        )

        assert subprocess.run(["/bin/sh", "-c", line], check=False).returncode == 0
        assert _gone([a, b, b_child])
        assert _alive(stranger.pid), "the line killed a process the command did not start"
        assert commandrun.survivor(started) == Gone(
            commandrun.NOTHING_ALIVE + commandrun.NONE_TAGGED
        )
    finally:
        stranger.kill()
        stranger.wait()
        _end_all(process, [a, b])


def test_a_child_started_with_an_empty_environment_is_not_found_and_is_not_said_to_be_gone(
    mark: str,
) -> None:
    """
    ``env -i`` and ``sudo`` start a process without the tag. It is not named,
    the group it shares is not aimed at, and with it alive in the session
    number the answer is never that the command is gone.
    """
    process, started = _tagged_shell(
        "env -i /bin/sleep 1000 & echo bare $!; sleep 1000 & echo a $!; wait",
        mark,
        _a_command_id(),
    )
    bare, a = _printed(process, "bare", "a")
    try:
        deadline = time.monotonic() + 5
        # By name: a process reads as having no environment while it execs.
        while Path(f"/proc/{bare}/comm").read_text() != "sleep\n" or len(_environ_of(a)) < 2:
            assert time.monotonic() < deadline, "env -i has not become sleep"
            time.sleep(0.01)
        assert _environ_of(bare) == [b""]
        os.kill(process.pid, signal.SIGKILL)
        process.wait()

        found = commandrun.survivor(started)
        # ``a`` shares the shell's group with ``bare``, so it is named alone.
        # An environment with nothing in it is not one that was read.
        assert found == Survivor(
            pid=process.pid,
            pgid=process.pid,
            members=1,
            pids=(a,),
            marked=(a,),
            shell=False,
            unread=1,
        )
        assert commandrun.kill_line(found) == f"kill -- {a}"
        assert str(bare) not in commandrun.look_line(found)
        assert subprocess.run(["/bin/sh", "-c", commandrun.kill_line(found)]).returncode == 0
        assert _gone([a])
        assert _alive(bare)

        left = commandrun.survivor(started)
        assert left == Unidentified(session=process.pid, members=1, unread=1, looked=True)
        said = commandrun.unidentified_line(left)
        assert "kill --" not in said and "1 could not be read" in said
    finally:
        _end_all(process, [bare, a])
    assert commandrun.survivor(started) == Gone(commandrun.NOTHING_ALIVE + commandrun.NONE_TAGGED)


@pytest.mark.parametrize("how", ["stop", "timeout", "exit", "close", "abandon"])
def test_a_process_that_left_the_session_is_counted_at_the_end_and_never_signalled(
    h: Harness, how: str
) -> None:
    """
    However a command ends, what it started in a session of its own is said
    to be alive, by pid, and is left alone.
    """
    tail = "" if how == "exit" else "; sleep 1000"
    command = h.start(
        f"setsid sleep 1000 >/dev/null 2>&1 & echo escaped $!; echo shell $${tail}",
        timeout_s=0.5 if how == "timeout" else 30.0,
    )
    escaped, shell = h.pids(command, "escaped", "shell")
    try:
        if how == "stop":
            h.runner.stop(command.id)
        elif how == "close":
            assert h.runner.close(10.0) == ()
        elif how == "abandon":
            assert h.runner.abandon(command.id)
        ended = h.wait(command)
        expected = {
            "stop": Cancelled(CancelledBy.OPERATOR),
            "timeout": TimedOut(),
            "exit": Exited(0),
            "close": Cancelled(CancelledBy.SHUTDOWN),
            "abandon": EndUnknown(),
        }[how]
        end = _end(ended)
        assert end.outcome == expected and end.unconfirmed is None
        assert end.escaped == (escaped,)
        assert _gone([shell])
        time.sleep(0.3)
        assert _alive(escaped), "pptmstr signalled a process outside the command's session"
        assert os.getsid(escaped) == escaped != shell
        on_disk = commandfile.read_command("s1", command.id, h.root)
        assert isinstance(on_disk, Command) and on_disk.end == end
        (remark,) = end.remarks
        assert f"pid {escaped}," in remark and f"-p {escaped}" in remark
        assert "pptmstr did not end it" in remark and "kill" not in remark
    finally:
        os.kill(escaped, signal.SIGKILL)


def test_an_end_with_nothing_outside_the_session_says_it_looked_and_one_unlooked_says_neither(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _end(h.run("sleep 1000 & echo done")).escaped == ()
    # No start time on the record: nothing says what is the command's.
    real = commandrun.started_now
    monkeypatch.setattr(
        commandrun,
        "started_now",
        lambda pid, at, tag=None: dataclasses.replace(real(pid, at, tag), start_ticks=None),
    )
    unplaced = _end(h.run("true"))
    assert unplaced.outcome == Exited(0) and unplaced.escaped is None
    monkeypatch.setattr(commandrun, "started_now", real)
    monkeypatch.setattr(commandrun, "_alive", lambda: None)
    assert _end(h.run("true")).escaped is None

    def unreadable() -> None:
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(commandrun, "_alive", unreadable)
    assert _end(h.run("true")).escaped is None


def test_the_look_for_what_left_the_session_holds_no_lock_and_is_the_workers(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    It reads a file for every process on the machine. With the runner's lock
    held, the thread that draws would wait on it at its next ``start``.
    """
    seen: list[tuple[str, bool]] = []
    real = commandrun.outside

    def watched(started: Any) -> Any:
        free = h.runner._changed.acquire(timeout=2.0)
        if free:
            h.runner._changed.release()
        seen.append((threading.current_thread().name, free))
        return real(started)

    monkeypatch.setattr(commandrun, "outside", watched)
    command = h.start("true")
    h.wait(command)
    assert seen == [(f"command-{command.id}", True)]


def test_a_survivor_with_its_shell_names_what_left_its_session_too(mark: str) -> None:
    process, started = _tagged_shell(
        "setsid sleep 1000 >/dev/null 2>&1 & echo out $!; sleep 1000 & echo a $!; wait",
        mark,
        _a_command_id(),
    )
    out, a = _printed(process, "out", "a")
    try:
        deadline = time.monotonic() + 5
        while os.getsid(out) != out:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        found = commandrun.survivor(started)
        assert found == Survivor(
            pid=process.pid,
            pgid=process.pid,
            members=2,
            groups=tuple(sorted((process.pid, out))),
            escaped=(out,),
        )
        assert found.alive == 3
        look = subprocess.run(
            ["/bin/sh", "-c", commandrun.look_line(found)], capture_output=True, text=True
        )
        shown = {int(row.split()[0]) for row in look.stdout.splitlines()[1:]}
        assert shown == {process.pid, a, out}, look.stdout + look.stderr
        assert subprocess.run(["/bin/sh", "-c", commandrun.kill_line(found)]).returncode == 0
        process.wait(timeout=5)
        assert _gone([a, out])
    finally:
        _end_all(process, [out, a])


def test_what_is_left_is_only_outside_the_session_and_is_still_a_survivor(mark: str) -> None:
    """
    The shell and its session are gone and a process that left it is not.
    """
    process, started = _tagged_shell(
        "setsid sleep 1000 >/dev/null 2>&1 & echo out $!", mark, _a_command_id()
    )
    (out,) = _printed(process, "out")
    try:
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while os.getsid(out) != out:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        found = commandrun.survivor(started)
        assert found == Survivor(
            pid=process.pid, pgid=process.pid, members=0, groups=(out,), escaped=(out,), shell=False
        )
        assert commandrun.kill_line(found) == f"kill -- -{out}"
        assert commandrun.look_line(found) == f"ps -o pid,pgid,sid,lstart,args -p {out}"
        assert commandrun.escaped(started) == (out,)
    finally:
        _end_all(process, [out])
    assert commandrun.survivor(started) == Gone(commandrun.NOTHING_ALIVE + commandrun.NONE_TAGGED)


_VALUE = "0123456789abcdef" * 4
_TAG = f"PPTMSTR_COMMAND={_VALUE}"


def _fake_environs(proc: Path, environs: dict[int, list[str] | str]) -> None:
    """
    What each process of a faked ``/proc`` was started with: its entries,
    ``"denied"`` for one only its own user may read, ``"went"`` for a process
    that exited between its two reads, ``"broken"`` for a read that failed
    for a reason that says nothing of the process.
    """
    assert os.geteuid() != 0, "root reads a file whatever its mode is"
    for pid, held in environs.items():
        path = proc / str(pid) / "environ"
        if held == "went":
            continue
        if held == "broken":
            path.mkdir()
            continue
        if held == "denied":
            path.write_bytes(_TAG.encode() + b"\0")
            path.chmod(0)
            continue
        assert isinstance(held, list)
        path.write_bytes(b"".join(entry.encode() + b"\0" for entry in held))


def _tagged_record(pid: int, ticks: int) -> Any:
    return dataclasses.replace(_recorded(pid, ticks), tag=commandrun.tag_digest(_VALUE))


def test_only_a_process_that_carries_exactly_the_tag_and_started_after_the_shell_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A leaderless session under the recorded number, with a stranger in it, and
    every way a process can nearly carry the tag.
    """
    processes = {
        # The session number the shell led. The shell, 50, is gone.
        51: ("S", 51, 50, 710),  # the command's
        52: ("S", 51, 50, 711),  # no tag, and in 51's group
        53: ("S", 53, 50, 712),  # cannot be read
        54: ("S", 54, 50, 699),  # the tag, and older than the shell
        55: ("S", 55, 50, 713),  # another command's tag
        56: ("S", 56, 50, 714),  # the tag and more
        57: ("S", 57, 50, 715),  # the tag under a longer name
        58: ("S", 58, 50, 700),  # the command's, started in the shell's own tick
        59: ("Z", 59, 50, 716),  # the command's, and dead
        # Other sessions.
        60: ("S", 60, 60, 720),  # the command's, beside a process that is not
        61: ("S", 60, 60, 721),
        70: ("S", 70, 70, 722),  # the command's, in a group all its own
        71: ("S", 70, 70, 723),
        80: ("S", 80, 80, 724),  # cannot be read, and nobody's session
    }
    proc = _fake_proc(tmp_path, monkeypatch, processes)
    _fake_environs(
        proc,
        {
            51: ["HOME=/h", _TAG, "PATH=/bin"],
            52: ["HOME=/h"],
            53: "denied",
            54: [_TAG],
            55: [_TAG[:-1] + "c"],
            56: [_TAG + "0"],
            57: ["X" + _TAG],
            58: [_TAG],
            59: [_TAG],
            60: [_TAG],
            61: [],
            70: [_TAG],
            71: ["A=1", _TAG],
            80: "denied",
        },
    )
    with pytest.raises(PermissionError):
        (proc / "53" / "environ").read_bytes()

    found = commandrun.survivor(_tagged_record(50, 700))
    assert found == Survivor(
        pid=50,
        pgid=50,
        members=2,
        groups=(58, 70),
        pids=(51, 60),
        escaped=(60, 70, 71),
        marked=(51, 58),
        shell=False,
        unread=1,
    )
    assert commandrun.kill_line(found) == "kill -- -58 -70 51 60"
    assert commandrun.look_line(found) == "ps -o pid,pgid,sid,lstart,args -p 51,58,60,70,71"
    assert commandrun.escaped(_tagged_record(50, 700)) == (60, 70, 71)

    # As a build without the tag recorded it: found as it was, never by tag.
    assert commandrun.survivor(_recorded(50, 700)) == Unidentified(session=50, members=8)
    assert commandrun.escaped(_recorded(50, 700)) is None


@pytest.mark.parametrize("unreadable", ["denied", "went"])
def test_an_environment_that_cannot_be_read_is_unread_and_never_gone_or_not_ours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unreadable: str
) -> None:
    proc = _fake_proc(tmp_path, monkeypatch, {51: ("S", 51, 50, 710), 52: ("S", 52, 50, 711)})
    _fake_environs(proc, {51: unreadable, 52: unreadable})
    assert commandrun._carries(51, commandrun.tag_digest(_VALUE)) is None
    found = commandrun.survivor(_tagged_record(50, 700))
    assert found == Unidentified(session=50, members=2, unread=2, looked=True)
    said = commandrun.unidentified_line(found)
    assert "2 could not be read" in said and "kill --" not in said
    assert "nothing on record tells" not in said

    # One of them read, and the command's: the other is counted and not named.
    proc = _fake_proc(
        tmp_path / "second", monkeypatch, {51: ("S", 51, 50, 710), 52: ("S", 51, 50, 711)}
    )
    _fake_environs(proc, {51: [_TAG], 52: unreadable})
    assert commandrun.survivor(_tagged_record(50, 700)) == Survivor(
        pid=50, pgid=50, members=1, pids=(51,), marked=(51,), shell=False, unread=1
    )

    # Read, and without it: said as that, and still not gone.
    proc = _fake_proc(tmp_path / "third", monkeypatch, {51: ("S", 51, 50, 710)})
    _fake_environs(proc, {51: ["HOME=/h"]})
    read = commandrun.survivor(_tagged_record(50, 700))
    assert read == Unidentified(session=50, members=1, unread=0, looked=True)
    assert "none of them carries its PPTMSTR_COMMAND." in commandrun.unidentified_line(read)
    assert "could not be read" not in commandrun.unidentified_line(read)
    assert "nothing on record tells the two apart" in commandrun.unidentified_line(
        Unidentified(session=50, members=1)
    )


def test_an_environment_read_that_fails_for_another_reason_is_not_a_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Out of descriptors, the read of every process fails. Counted as "not
    carrying it", a command that is running everywhere would be gone.
    """
    proc = _fake_proc(tmp_path, monkeypatch, {60: ("S", 60, 60, 710)})
    _fake_environs(proc, {60: "broken"})
    found = commandrun.survivor(_tagged_record(50, 700))
    assert isinstance(found, NotLooked) and "IsADirectoryError" in found.reason
    assert commandrun.escaped(_tagged_record(50, 700)) is None


def test_a_session_number_in_other_hands_holds_nothing_of_the_commands_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The shell's pid is another leader's now, so its session emptied first.
    What is in that number is the newcomer's: unread there is not this
    command's unread, and a tagged process there is one that left.
    """
    processes = {50: ("S", 50, 50, 900), 51: ("S", 51, 50, 901), 52: ("S", 52, 50, 902)}
    proc = _fake_proc(tmp_path, monkeypatch, processes)
    _fake_environs(proc, {50: [], 51: "denied", 52: ["A=1"]})
    assert commandrun.survivor(_tagged_record(50, 700)) == Gone(
        commandrun.NUMBER_REUSED + commandrun.NONE_TAGGED
    )
    (proc / "52" / "environ").write_bytes(_TAG.encode() + b"\0")
    assert commandrun.survivor(_tagged_record(50, 700)) == Survivor(
        pid=50, pgid=50, members=0, groups=(52,), escaped=(52,), shell=False
    )


def test_a_zombie_shell_with_an_empty_session_and_a_process_outside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _fake_proc(tmp_path, monkeypatch, {50: ("Z", 50, 50, 700), 60: ("S", 60, 60, 710)})
    _fake_environs(proc, {50: [], 60: [_TAG]})
    assert commandrun.survivor(_tagged_record(50, 700)) == Survivor(
        pid=50, pgid=50, members=0, groups=(60,), escaped=(60,), shell=False
    )
    (proc / "60" / "environ").write_bytes(b"A=1\0")
    assert commandrun.survivor(_tagged_record(50, 700)) == Gone(
        commandrun.NOTHING_ALIVE + commandrun.NONE_TAGGED
    )


def test_a_tagged_process_that_joined_another_group_does_not_put_that_group_on_the_kill_line(
    mark: str,
) -> None:
    """
    What a contained agent can do with a command's id: start a process of its
    own that carries it, and move that process into the group of something
    else in its session. The line names the process and not the group.
    """
    command_id = _a_command_id()
    process, started = _tagged_shell("true", mark, command_id)
    process.wait()
    assert process.stdout is not None
    process.stdout.close()
    # Not the command's: started afterwards by this test. A leader and a
    # bystander in one group, without the tag, and a process with it that
    # first has a group of its own and then joins theirs.
    tagged = (
        "import os, sys, time\n"
        "os.setpgid(0, 0)\n"
        "print('own', os.getpid(), flush=True)\n"
        "sys.stdin.readline()\n"
        "os.setpgid(0, int(sys.argv[1]))\n"
        "print('joined', os.getpid(), flush=True)\n"
        "time.sleep(1000)\n"
    )
    leader = (
        "import os, subprocess, sys, time\n"
        "bystander = subprocess.Popen(['/bin/sleep', '1000'], stdout=subprocess.DEVNULL)\n"
        "print('bystander', bystander.pid, flush=True)\n"
        "subprocess.Popen(\n"
        "    [sys.executable, '-c', sys.argv[1], str(os.getpgid(0))],\n"
        "    env={**os.environ, 'PPTMSTR_COMMAND': sys.argv[2]},\n"
        ")\n"
        "time.sleep(1000)\n"
    )
    agent = subprocess.Popen(
        [sys.executable, "-c", leader, tagged, _VALUES[command_id]],
        start_new_session=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env={**os.environ, MARK: mark},
    )
    bystander, own = _printed(agent, "bystander", "own")
    try:
        alone = commandrun.survivor(started)
        assert isinstance(alone, Survivor) and not alone.shell
        assert (alone.groups, alone.pids, alone.escaped) == ((own,), (), (own,))

        assert agent.stdin is not None
        agent.stdin.write(b"\n")
        agent.stdin.flush()
        _printed(agent, "joined")
        assert os.getpgid(own) == agent.pid == os.getpgid(bystander)
        found = commandrun.survivor(started)
        assert isinstance(found, Survivor)
        assert (found.groups, found.pids, found.escaped) == ((), (own,), (own,))
        assert commandrun.kill_line(found) == f"kill -- {own}"
        assert subprocess.run(["/bin/sh", "-c", commandrun.kill_line(found)]).returncode == 0
        assert _gone([own])
        assert _alive(bystander) and _alive(agent.pid), "the line ended what carries no tag"
    finally:
        if agent.stdin is not None:
            agent.stdin.close()
        _end_all(agent, [bystander, own])


def test_a_process_that_carried_the_tag_before_the_shell_started_is_not_the_commands(
    mark: str,
) -> None:
    """
    An id is known from the moment a command is asked. What was started with
    it before the operator approved anything was not started by the command.
    """
    command_id = _a_command_id()
    early = subprocess.Popen(
        ["sleep", "1000"],
        start_new_session=True,
        env={MARK: mark, commandrun.COMMAND_TAG: command_id},
    )
    try:
        # A tick is a hundredth of a second: the shell is started well clear.
        time.sleep(0.05)
        process, started = _tagged_shell("true", mark, command_id)
        process.wait()
        assert process.stdout is not None
        process.stdout.close()
        assert commandrun.survivor(started) == Gone(
            commandrun.NOTHING_ALIVE + commandrun.NONE_TAGGED
        )
        assert commandrun.escaped(started) == ()
    finally:
        early.kill()
        early.wait()


# -- the property: giving a command up does not read /proc where it is asked -------


def test_an_abandon_finds_what_to_kill_on_a_thread_of_its_own_with_no_lock_held(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``abandon`` is called by the thread that draws. The listing is a file read
    for every process there is.
    """
    command = h.start("echo shell $$; sleep 1000")
    (shell,) = h.pids(command, "shell")
    seen: list[tuple[bool, bool]] = []
    real = commandrun._session_groups
    caller = threading.get_ident()

    def watched(sid: int) -> list[int]:
        free = h.runner._changed.acquire(timeout=2.0)
        if free:
            h.runner._changed.release()
        if sid == shell and threading.current_thread().name == f"command-abandon-{command.id}":
            seen.append((threading.get_ident() != caller, free))
        return real(sid)

    monkeypatch.setattr(commandrun, "_session_groups", watched)
    assert h.runner.abandon(command.id)
    assert _outcome(h.wait(command)) == EndUnknown()
    assert seen == [(True, True)]
    assert _gone([shell])


def test_an_abandon_that_can_start_no_thread_kills_before_it_returns(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = h.start("echo shell $$; trap '' TERM; sleep 1000 & echo child $!; wait")
    shell, child = h.pids(command, "shell", "child")
    real = threading.Thread.start

    def no_thread(thread: threading.Thread) -> None:
        if thread.name.startswith("command-abandon-"):
            raise RuntimeError("can't start new thread")
        real(thread)

    sent: list[int] = []
    real_signal = commandrun._signal_group

    def recorded(pgid: int, sig: int) -> None:
        sent.append(threading.get_ident())
        real_signal(pgid, sig)

    monkeypatch.setattr(threading.Thread, "start", no_thread)
    monkeypatch.setattr(commandrun, "_signal_group", recorded)
    assert h.runner.abandon(command.id)
    assert sent[:1] == [threading.get_ident()]
    assert _outcome(h.wait(command)) == EndUnknown()
    assert _gone([shell, child])


def test_an_environment_that_reads_as_empty_is_read_again_and_is_never_one_without_the_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A process has no environment to read for the moment it execs in. Taken as
    "does not carry it", a look at that moment finds a running command gone,
    and writes that down for good.
    """
    proc = _fake_proc(tmp_path, monkeypatch, {60: ("S", 60, 60, 710)})
    _fake_environs(proc, {60: []})
    slept: list[float] = []

    def exec_finishes(seconds: float) -> None:
        slept.append(seconds)
        (proc / "60" / "environ").write_bytes(_TAG.encode() + b"\0")

    monkeypatch.setattr(commandrun.time, "sleep", exec_finishes)
    assert commandrun._carries(60, commandrun.tag_digest(_VALUE)) is True
    assert slept == [commandrun._EXEC_S] and 0 < commandrun._EXEC_S < 0.05
    assert commandrun.survivor(_tagged_record(50, 700)) == Survivor(
        pid=50, pgid=50, members=0, groups=(60,), escaped=(60,), shell=False
    )

    # Empty both times, as under ``env -i``: unread, in the session number or out.
    monkeypatch.setattr(commandrun.time, "sleep", slept.append)
    proc = _fake_proc(
        tmp_path / "second", monkeypatch, {51: ("S", 51, 50, 710), 60: ("S", 60, 60, 711)}
    )
    _fake_environs(proc, {51: [], 60: []})
    assert commandrun._carries(51, commandrun.tag_digest(_VALUE)) is None
    assert commandrun.survivor(_tagged_record(50, 700)) == Unidentified(
        session=50, members=1, unread=1, looked=True
    )
    # One that has an environment, and not the tag, is read.
    (proc / "51" / "environ").write_bytes(b"A=1\0")
    assert commandrun._carries(51, commandrun.tag_digest(_VALUE)) is False


def test_a_tagged_process_in_a_group_that_is_nobodys_to_signal_is_named_by_its_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Group 0 is one this pid namespace cannot see and 1 is init's. After
    ``kill --`` the first is the operator's own shell's group and the second
    every process they may signal.
    """
    proc = _fake_proc(tmp_path, monkeypatch, {60: ("S", 0, 60, 710), 61: ("S", 1, 61, 711)})
    _fake_environs(proc, {60: [_TAG], 61: [_TAG]})
    found = commandrun.survivor(_tagged_record(50, 700))
    assert found == Survivor(
        pid=50, pgid=50, members=0, pids=(60, 61), escaped=(60, 61), shell=False
    )
    assert commandrun.kill_line(found) == "kill -- 60 61"


def test_with_its_shell_there_the_session_is_named_whole_whatever_its_processes_carry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    While the shell holds the number the session is the command's, a child
    started with nothing in its environment included: its groups are named as
    groups, and nothing in it is picked out by pid.
    """
    processes = {
        50: ("S", 50, 50, 700),
        51: ("S", 50, 50, 710),
        52: ("S", 50, 50, 711),
        60: ("S", 60, 60, 712),
    }
    proc = _fake_proc(tmp_path, monkeypatch, processes)
    _fake_environs(proc, {50: [_TAG], 51: [_TAG], 52: ["A=1"], 60: [_TAG]})
    found = commandrun.survivor(_tagged_record(50, 700))
    assert found == Survivor(pid=50, pgid=50, members=3, groups=(50, 60), escaped=(60,))
    assert commandrun.kill_line(found) == "kill -- -50 -60"


def _fake_cgroups(proc: Path, groups: dict[int, str]) -> None:
    for pid, group in groups.items():
        (proc / str(pid) / "cgroup").write_text(group + "\n")


_GROUP = "0::/user.slice/a-terminal.scope"


def test_an_unread_process_outside_the_session_is_counted_only_by_the_shells_control_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Most of what cannot be read on a machine is another user's daemons. What
    is counted beside a command is what its shell's control group ties to it,
    and that is never named as the command's and never makes it gone.
    """
    processes = {
        60: ("S", 60, 60, 720),  # unread, in the group, after the shell: tied
        61: ("S", 61, 61, 721),  # unread, in another group
        62: ("S", 62, 62, 699),  # unread, in the group, older than the shell
        63: ("S", 63, 63, 722),  # in the group, read, and without the tag
        64: ("S", 64, 64, 723),  # unread, and its group cannot be read either
        65: ("S", 65, 65, 724),  # unread, in the group, and a thread of the kernel's
        66: ("S", 66, 66, 725),  # unread, in the group, after the shell: tied
    }
    proc = _fake_proc(tmp_path, monkeypatch, processes)
    stat = proc / "65" / "stat"
    fields = stat.read_text().split()
    # The flags are the ninth field of a real one; the name here is four words.
    assert fields[11] == "0"
    fields[11] = str(0x00200000)
    stat.write_text(" ".join(fields) + "\n")
    assert commandrun._process(65).kernel and not commandrun._process(60).kernel  # type: ignore[union-attr]
    _fake_environs(
        proc,
        {60: "denied", 61: "denied", 62: "denied", 63: ["A=1"], 64: "denied", 65: "denied"}
        | {66: "denied"},
    )
    _fake_cgroups(proc, {60: _GROUP, 61: "0::/system.slice/cron.service", 62: _GROUP})
    _fake_cgroups(proc, {63: _GROUP, 65: _GROUP, 66: _GROUP})
    record = dataclasses.replace(_tagged_record(50, 700), cgroup=_GROUP)

    found = commandrun.survivor(record)
    assert found == Unidentified(session=50, members=0, unread=0, looked=True, outside=(60, 66))
    said = commandrun.unidentified_line(found)
    assert said == (
        "its shell (pid 50) is gone, and no process that could be read carries its"
        " PPTMSTR_COMMAND. 2 processes started after it, in the control group it was started"
        " in, cannot be read for its PPTMSTR_COMMAND (pid 60, 66): what it left running, or"
        " something else started beside it. To see: ps -o pid,pgid,sid,lstart,args -p 60,66"
        " -- pptmstr names nothing to kill, because it cannot say this is the command"
    )
    assert commandrun.outside(record) == ((), (60, 66))
    # With no group on the record nothing ties them, and the answer says what
    # such a look does not find rather than that there is nothing.
    assert commandrun.survivor(_tagged_record(50, 700)) == Gone(
        commandrun.NOTHING_ALIVE + commandrun.NONE_TAGGED
    )
    assert commandrun.outside(_tagged_record(50, 700)) == ((), ())

    # The shell's number in other hands: what is in that session is nobody's
    # of this command, and what is tied is still why it is not gone.
    (proc / "50").mkdir()
    (proc / "50" / "stat").write_text(f"50 (other) S 1 50 50 {' '.join(['0'] * 15)} 900 0 0\n")
    (proc / "50" / "environ").write_bytes(b"A=1\0")
    assert commandrun.survivor(record) == Unidentified(
        session=50, members=0, unread=0, looked=True, outside=(60, 66)
    )
    assert commandrun.survivor(_tagged_record(50, 700)) == Gone(
        commandrun.NUMBER_REUSED + commandrun.NONE_TAGGED
    )


def test_an_unread_process_in_the_session_number_is_counted_there_and_not_as_outside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _fake_proc(tmp_path, monkeypatch, {51: ("S", 51, 50, 710), 60: ("S", 60, 60, 720)})
    _fake_environs(proc, {51: "denied", 60: "denied"})
    _fake_cgroups(proc, {51: _GROUP, 60: _GROUP})
    record = dataclasses.replace(_tagged_record(50, 700), cgroup=_GROUP)
    assert commandrun.outside(record) == ((), (60,))
    assert commandrun.survivor(record) == Unidentified(
        session=50, members=1, unread=1, looked=True, outside=(60,)
    )


def test_what_is_tied_is_said_beside_what_is_in_the_session_and_only_four_are_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processes = {51: ("S", 51, 50, 710)} | {pid: ("S", pid, pid, 720) for pid in range(60, 66)}
    proc = _fake_proc(tmp_path, monkeypatch, processes)
    _fake_environs(proc, {51: ["A=1"]} | {pid: "denied" for pid in range(60, 66)})
    _fake_cgroups(proc, {pid: _GROUP for pid in range(60, 66)})
    found = commandrun.survivor(dataclasses.replace(_tagged_record(50, 700), cgroup=_GROUP))
    assert found == Unidentified(
        session=50, members=1, unread=0, looked=True, outside=(60, 61, 62, 63, 64, 65)
    )
    said = commandrun.unidentified_line(found)
    assert "1 process is alive in the session number it led" in said
    assert ". Also, 6 processes started after it" in said
    assert "(pid 60, 61, 62, 63 and 2 more)" in said and "64" not in said
    assert said.endswith("because it cannot say this is the command")


def test_a_shell_that_exits_between_the_listing_and_the_look_at_it_is_not_on_the_kill_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The listing sees the shell alive and the look at it, a moment later, finds
    nothing: it has exited and been reaped. The shell carries the tag like
    everything it started, and a line that kills must not name a pid that is
    by then free to be anyone's.
    """
    proc = _fake_proc(
        tmp_path,
        monkeypatch,
        {50: ("S", 50, 50, 700), 51: ("S", 51, 50, 710), 52: ("S", 52, 52, 711)},
    )
    _fake_environs(proc, {50: [_TAG], 51: [_TAG], 52: [_TAG]})
    real = commandrun._process
    asked: list[int] = []

    def exits_after_the_listing(pid: int) -> Any:
        asked.append(pid)
        if pid == 50 and asked.count(50) > 1:
            return None
        if pid == 52 and asked.count(52) > 1:
            # Its number, already another process's.
            return dataclasses.replace(real(pid), start_ticks=999)  # type: ignore[arg-type]
        return real(pid)

    monkeypatch.setattr(commandrun, "_process", exits_after_the_listing)
    found = commandrun.survivor(_tagged_record(50, 700))
    assert found == Survivor(pid=50, pgid=50, members=1, groups=(51,), marked=(51,), shell=False)
    assert commandrun.kill_line(found) == "kill -- -51"
    assert asked.count(51) == 2


def test_an_environment_longer_than_is_read_is_unread_and_not_one_without_the_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The length of ``environ`` is the process's to decide. A read with no bound
    is memory pptmstr spends at a stranger's word; a read cut short that
    answered "does not carry it" would be the way to hide: pad, then the tag.
    """
    assert commandrun.MAX_ENVIRON_BYTES == 8 << 20
    monkeypatch.setattr(commandrun, "MAX_ENVIRON_BYTES", 256)
    proc = _fake_proc(
        tmp_path,
        monkeypatch,
        {51: ("S", 51, 50, 710), 52: ("S", 52, 50, 711), 53: ("S", 53, 50, 712)},
    )
    digest = commandrun.tag_digest(_VALUE)
    padded = b"PAD=" + b"x" * 300 + b"\0" + _TAG.encode() + b"\0"
    (proc / "51" / "environ").write_bytes(padded)
    (proc / "52" / "environ").write_bytes(_TAG.encode() + b"\0" + b"PAD=" + b"x" * 300 + b"\0")
    exact = b"A=" + b"y" * (256 - 3 - len(_TAG) - 1) + b"\0" + _TAG.encode() + b"\0"
    assert len(exact) == 256
    (proc / "53" / "environ").write_bytes(exact)
    reads: list[int] = []
    real_open = open

    def counting(path: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        handle = real_open(path, mode, *args, **kwargs)
        if str(path).endswith("environ"):
            real_read = handle.read

            def read(size: int = -1) -> bytes:
                reads.append(size)
                return real_read(size)  # type: ignore[no-any-return]

            return _Reads(handle, read)
        return handle

    monkeypatch.setattr("builtins.open", counting)
    assert commandrun._carries(51, digest) is None
    assert commandrun._carries(52, digest) is None
    assert commandrun._carries(53, digest) is True
    assert reads == [257, 257, 257]
    monkeypatch.undo()
    monkeypatch.setattr(commandrun, "_PROC", str(proc))
    monkeypatch.setattr(commandrun, "MAX_ENVIRON_BYTES", 256)
    found = commandrun.survivor(_tagged_record(50, 700))
    assert found == Survivor(
        pid=50, pgid=50, members=1, groups=(53,), marked=(53,), shell=False, unread=2
    )


class _Reads:
    """A file whose ``read`` is another's, and that is otherwise itself."""

    def __init__(self, handle: Any, read: Any) -> None:
        self._handle, self.read = handle, read

    def __enter__(self) -> _Reads:
        return self

    def __exit__(self, *exc: object) -> None:
        self._handle.close()
