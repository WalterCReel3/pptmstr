#!/usr/bin/env python3
"""
When the host dies mid-park, does the CLI notice -- or does it keep running?

``planning/2026-08-22-an-approval-parked-overnight-is-not-a-wedged-host.md``
ranks host-death detection and stops at candidate 3: with the host process fully
dead, nothing of ours survives to notice, so only the CLI can. Whether the CLI
treats its closed pipes as abort-now is unreadable -- the binary is bundled. That
record names this probe P2, and calls it the discriminator: if pipe-close ends
the CLI, host death self-cleans and item 1's drain-stall watchdog never needs a
deny arm; if the CLI survives, a warn-only watchdog leaves an orphan holding a
session open and the loud fallback has to come from outside the process.

**The shape.** This script is the observer, not the subject. It spawns a *host*
child (itself, ``--host``), which opens a real ``ClaudeSDKClient`` whose
PreToolUse hook parks forever -- the gate's own posture, an approval pending and
nobody answering. The host announces the park on its stdout, the observer
``SIGKILL``s it (no teardown runs; that is the point -- a graceful stop is not
host death), and then watches the processes the host had started.

**The observable is process liveness, not narration.** Before the kill the
observer enumerates the host's descendants out of ``/proc`` and records each
one's ``starttime``, so a pid recycled during the watch is detected rather than
mistaken for the CLI still running. After the kill it polls until each is gone
or the window closes. The CLI also inherits the host's stderr, which the observer
supplies as a file it holds open itself -- so anything the CLI says on the way out
survives the host and is captured verbatim.

**The kill needs a control, or the verdict names the wrong cause.** "The CLI was
gone N seconds after the host died" is also what "the CLI exits N+HOLD_S seconds
into any park" looks like. So the observer holds the park for ``HOLD_S`` first
and requires every descendant to still be running at the end of it; if one is
not, the run is INCONCLUSIVE and says so rather than crediting the kill.

**What ONE run of this can settle:** whether the CLI process exits on its own
within the watch window after the host's stdin/stdout pipes close, on this
machine and this CLI version.

**What it cannot settle:** anything about a slower reaper. A CLI that exits at
five minutes is reported here as surviving, because the window is shorter than
that; the verdict names the window it used. It also does not distinguish "exited
because the pipes closed" from "exited because it was orphaned" -- both follow
from the same kill, and separating them needs a second probe that closes the
pipes without killing the host.

Costs one real turn against a trivial prompt.
Linux-only: the descendant walk reads ``/proc``.

Usage:  .venv/bin/python scripts/verify_host_death_visibility.py
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# One tool call, so the gate fires exactly once and then holds.
PROMPT = "Run the bash command `echo pptmstr-probe` and then reply with just the word done."

# Long enough that nothing expires during the run: the park must still be open
# when the host is killed, or the probe measures a timeout instead of a death.
PARK_TIMEOUT_S = 604800.0

# How long the observer waits for the host to reach the park before giving up.
PARK_DEADLINE_S = 120.0

# The control window: the park is held this long with the host alive, and every
# descendant must survive it. Comfortably longer than the post-kill exit this
# probe has observed, so the two cannot be confused for each other.
HOLD_S = 20.0

# How long the CLI is watched after the host dies. A CLI that outlives this is
# reported as surviving *this window*; the verdict says so in as many words.
WATCH_S = 60.0

POLL_S = 0.25


@dataclass(frozen=True)
class Proc:
    """
    One process the host started, pinned to the boot-clock start time that makes
    its pid unambiguous across a recycle.
    """

    pid: int
    starttime: int
    cmd: str


def main() -> int:
    if "--host" in sys.argv[1:]:
        return _host_main()
    return _observe()


# --------------------------------------------------------------------------
# The observer
# --------------------------------------------------------------------------


def _observe() -> int:
    t0 = time.monotonic()
    events: list[tuple[float, str]] = []

    def mark(detail: str) -> None:
        events.append((time.monotonic() - t0, detail))

    stderr_path = Path("/tmp") / f"pptmstr-p2-cli-stderr-{os.getpid()}.log"
    stderr_file = stderr_path.open("w+")
    mark(f"spawning host, CLI stderr -> {stderr_path}")

    host = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--host"],
        stdout=subprocess.PIPE,
        stderr=stderr_file,
        text=True,
        bufsize=1,
    )

    parked = False
    survived_hold = False
    host_note: str | None = None
    descendants: list[Proc] = []
    gone_at: dict[int, float] = {}

    # readline() on a blocking pipe returns only on a line or on EOF, so a loop
    # condition cannot enforce PARK_DEADLINE_S: a host that hangs before printing
    # would block the observer forever, and the cleanup that kills the CLI lives
    # past this loop. The timer closes the pipe by killing the host, which turns
    # the hang into the EOF the `not parked` arm already reports.
    expired = threading.Event()

    def _expire() -> None:
        if host.poll() is None:
            expired.set()
            host.kill()

    timer = threading.Timer(PARK_DEADLINE_S, _expire)
    timer.daemon = True
    timer.start()

    try:
        assert host.stdout is not None
        while True:
            line = host.stdout.readline()
            if not line:
                mark("park deadline expired" if expired.is_set() else "host stdout closed")
                break
            try:
                note = json.loads(line)
            except json.JSONDecodeError:
                mark(f"host: {line.strip()[:90]}")
                continue
            mark(f"host: {note}")
            if note.get("event") == "parked":
                parked = True
                host_note = str(note.get("tool", ""))
                break
            if note.get("event") == "error":
                host_note = str(note.get("detail", ""))
                break

        timer.cancel()

        if parked:
            descendants = _descendants(host.pid)
            mark(f"descendants at the park: {[(p.pid, p.cmd) for p in descendants]}")

            time.sleep(HOLD_S)
            survived_hold = descendants != [] and all(_alive(p) for p in descendants)
            mark(f"held the park {HOLD_S:.0f}s, host alive, all still running={survived_hold}")

            os.kill(host.pid, signal.SIGKILL)
            killed_at = time.monotonic() - t0
            host.wait(timeout=10)
            mark(f"host SIGKILLed, exit={host.returncode}")

            watch_until = time.monotonic() + WATCH_S
            pending = {p.pid: p for p in descendants}
            while pending and time.monotonic() < watch_until:
                for proc in list(pending.values()):
                    if not _alive(proc):
                        gone_at[proc.pid] = time.monotonic() - t0 - killed_at
                        mark(f"pid {proc.pid} gone {gone_at[proc.pid]:.2f}s after the kill")
                        del pending[proc.pid]
                time.sleep(POLL_S)
            for proc in pending.values():
                mark(f"pid {proc.pid} STILL ALIVE after {WATCH_S:.0f}s ({proc.cmd})")
    finally:
        timer.cancel()
        # Enumerated before the host dies, not after: on the paths that never
        # parked -- a deadline, an error -- `descendants` is empty while the host
        # may still have a live CLI under it, and once the host is gone there is
        # no ppid left to find it by.
        stragglers = _descendants(host.pid) if host.poll() is None else []
        if host.poll() is None:
            os.kill(host.pid, signal.SIGKILL)
            host.wait(timeout=10)
        # Only processes this script's own host started, and only the ones still
        # running. Leaving one would leak a live CLI session.
        leaked = [p for p in [*descendants, *stragglers] if _alive(p)]
        for proc in leaked:
            mark(f"cleanup: killing surviving pid {proc.pid}")
            try:
                os.kill(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stderr_file.flush()
        stderr_file.seek(0)
        cli_stderr = stderr_file.read()
        stderr_file.close()

    _report(events, parked, survived_hold, host_note, descendants, gone_at, cli_stderr)
    return 0


def _report(
    events: list[tuple[float, str]],
    parked: bool,
    survived_hold: bool,
    host_note: str | None,
    descendants: list[Proc],
    gone_at: dict[int, float],
    cli_stderr: str,
) -> None:
    print("\n=== timeline (seconds from start) ===")
    for at, detail in events:
        print(f"  {at:7.2f}  {detail}")

    print("\n=== what the CLI wrote to the inherited stderr ===")
    print(cli_stderr.strip() or "  (nothing)")

    print("\n=== verdict ===")
    if not parked:
        print("  INCONCLUSIVE: the host never reached a parked approval, so no park was")
        print(f"  open when it died. detail={host_note!r}")
        return
    if not descendants:
        print("  INCONCLUSIVE: the park was reached but no descendant process was found")
        print("  under the host, so there was nothing to watch. The /proc walk is the")
        print("  suspect before the CLI is.")
        return
    if not survived_hold:
        print(f"  INCONCLUSIVE: a process the host started exited during the {HOLD_S:.0f}s hold,")
        print("  before anything was killed. An exit after the kill cannot be attributed to")
        print("  the kill when the park alone already ends one.")
        return

    survivors = [p for p in descendants if p.pid not in gone_at]
    if not survivors:
        latest = max(gone_at.values())
        print(f"  SELF-DETECTS: every process the host started was gone {latest:.2f}s after")
        print(f"  the host was killed, having survived the same park for {HOLD_S:.0f}s while the")
        print("  host was alive. Host death, not the park, is what ends the CLI, and it does")
        print("  not leave a live one behind -- so item 1's drain-stall watchdog needs no deny")
        print("  arm to reclaim a session. Warn-only is sufficient for this failure.")
        return
    print(f"  SURVIVES-ORPHANED: {len(survivors)} of {len(descendants)} process(es) the host")
    print(f"  started were still running {WATCH_S:.0f}s after it was SIGKILLed, with an")
    print("  approval parked:")
    for proc in survivors:
        print(f"    pid {proc.pid}  {proc.cmd}")
    print("  Pipe-close is not an abort-now signal within this window, so nothing inside a")
    print("  dead host reclaims the session. A warn-only watchdog cannot fix this; the loud")
    print("  fallback has to be external. (This run was killed by the probe's own cleanup.)")


# --------------------------------------------------------------------------
# /proc, read rather than inferred
# --------------------------------------------------------------------------


def _stat_fields(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    # comm can contain spaces and parentheses, so split on the last ')'.
    close = raw.rfind(")")
    if close < 0:
        return None
    return [raw[: raw.find("(")].strip(), raw[raw.find("(") + 1 : close]] + raw[close + 2 :].split()


def _proc_of(pid: int) -> Proc | None:
    fields = _stat_fields(pid)
    if fields is None:
        return None
    # After the two synthesised entries, field[i] is stat's field i+1: state is
    # index 2, ppid index 3, starttime index 21.
    try:
        starttime = int(fields[21])
    except (IndexError, ValueError):
        return None
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except OSError:
        cmdline = fields[1]
    return Proc(pid=pid, starttime=starttime, cmd=cmdline.strip()[:100] or fields[1])


def _descendants(root_pid: int) -> list[Proc]:
    """
    Every live process under `root_pid`, by walking /proc once and following ppid.
    """
    parent_of: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        fields = _stat_fields(int(entry.name))
        if fields is None:
            continue
        try:
            parent_of[int(entry.name)] = int(fields[3])
        except (IndexError, ValueError):
            continue

    found: list[Proc] = []
    frontier = {root_pid}
    while frontier:
        children = {pid for pid, ppid in parent_of.items() if ppid in frontier}
        children -= {p.pid for p in found}
        for pid in sorted(children):
            proc = _proc_of(pid)
            if proc is not None:
                found.append(proc)
        frontier = children
    return found


def _alive(proc: Proc) -> bool:
    """
    True only if `proc.pid` is the same process that was recorded, not a recycle.
    """
    fields = _stat_fields(proc.pid)
    if fields is None:
        return False
    try:
        if int(fields[21]) != proc.starttime:
            return False
    except (IndexError, ValueError):
        return False
    # An orphan is reparented and reaped by init, but a zombie can be observed in
    # the gap. A zombie has exited, which is what the verdict is asking about.
    return fields[2] != "Z"


# --------------------------------------------------------------------------
# The host: the subject of the probe, killed while parked
# --------------------------------------------------------------------------


def _host_main() -> int:
    return asyncio.run(_host())


async def _host() -> int:
    from claude_agent_sdk import (
        ClaudeAgentOptions,
        ClaudeSDKClient,
        HookContext,
        HookMatcher,
    )
    from claude_agent_sdk.types import HookInput, HookJSONOutput

    def say(**note: Any) -> None:
        print(json.dumps(note), flush=True)

    async def park(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        say(event="parked", tool=str(hook_input.get("tool_name", "")))
        # The gate's posture with nobody answering: a pending approval, held.
        # Nothing here ever resolves it -- the observer kills this process instead.
        await asyncio.Event().wait()
        raise AssertionError("unreachable: the park is never resolved")

    options = ClaudeAgentOptions(
        model="claude-haiku-4-5-20251001",
        permission_mode="dontAsk",
        allowed_tools=["Bash"],
        hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[park], timeout=PARK_TIMEOUT_S)]},
        max_turns=2,
    )

    try:
        async with ClaudeSDKClient(options=options) as client:
            say(event="connected")
            await client.query(PROMPT)
            async for _ in client.receive_response():
                pass
    except Exception as exc:  # noqa: BLE001 - the observer needs the reason, not a traceback
        say(event="error", detail=f"{type(exc).__name__}: {exc}")
        return 1
    say(event="ended", detail="the response completed without parking")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
