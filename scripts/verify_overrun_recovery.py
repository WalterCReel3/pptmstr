#!/usr/bin/env python3
"""
What actually reaches ``driver.py`` when one CLI message exceeds the buffer
ceiling, and is the stream resumable afterwards?

The operator lost a real session to this. ``planning`` has no record of it yet;
this probe is what a record would have to cite.

**The question is not "does the guard fire".** That is readable:
``subprocess_cli.py`` ``_read_messages_impl`` raises ``SDKJSONDecodeError`` from
its ``guard`` closure. The two things reading cannot settle are what TYPE
survives the trip out to our ``async for``, and whether iteration can continue
once it has fired -- and the second is the whole basis for choosing between a
transport-level skip and a session resume.

**Why a fake CLI rather than a real one.** The failure is a property of the
transport's framing, not of anything the model does. A fake CLI makes the
oversized line exact, reproducible and free, and it lets the same run also emit
a message AFTER the oversized one -- which is the only way to tell "the stream
recovered" from "the stream ended". A real CLI cannot be made to do that on
demand. What the fake cannot establish is whether the real CLI fails identically,
or whether a resumed session replays the same oversized message; nothing here
claims to answer either.

The recovery work this was written for was descoped before a recommendation was
reached -- see ``notes/overrun-probe.md``, which records what these cases measured
and what was left open.

Three cases, each moving one variable:

A. Real ``SubprocessCLITransport``, iterated directly. Establishes what it raises
   and whether the generator yields anything after it. It does NOT distinguish
   which of the two guards fired -- the completed-line ``len(line)`` one or the
   in-flight ``pending_len`` one -- because the inner ``ValueError``'s reported
   length is not captured. That distinction matters only to a skip design.
B. Real ``Query`` over that same real transport. Establishes the type that
   arrives at a consumer -- the one claim the whole "just catch it" fix rests on.
C. Same as B, but the oversized line is under a RAISED ceiling. Establishes that
   the ceiling is the only thing separating the two outcomes, i.e. that case A/B
   are measuring the buffer guard and not some other failure of the fake CLI.

Print-only: nothing touches app state, and no API tokens are spent.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_agent_sdk import ClaudeAgentOptions  # noqa: E402
from claude_agent_sdk._internal.query import Query  # noqa: E402
from claude_agent_sdk._internal.transport.subprocess_cli import (  # noqa: E402
    SubprocessCLITransport,
)

# Large enough that the in-flight guard must trip well before the line
# terminates, so the reported length distinguishes which of the two guards
# fired. At 3 MiB against a 1 MiB ceiling the completed-line guard would report
# ~3 MiB and the pending_len guard ~1 MiB.
OVERSIZE_CHARS = 3 * 1024 * 1024

FAKE_CLI = '''#!/usr/bin/env python3
"""A CLI stand-in that emits NDJSON on stdout and nothing else."""
import json, sys, time

if "-v" in sys.argv:
    print("0.0.0-fake (Claude Code)")
    sys.exit(0)

oversize = int(sys.argv[sys.argv.index("--oversize") + 1])

def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()

# A small message BEFORE, so we can prove the stream was healthy first.
emit({"type": "system", "subtype": "init", "session_id": "fake-1", "marker": "before"})
# The oversized one. A single line, no embedded newline.
emit({"type": "assistant", "marker": "oversized", "payload": "x" * oversize})
# A small message AFTER. If this is ever delivered, the stream recovered.
emit({"type": "system", "subtype": "probe", "session_id": "fake-1", "marker": "after"})
emit({"type": "result", "subtype": "success", "marker": "terminal", "is_error": False})

# Stay alive briefly so the reader is not racing process exit for the verdict.
time.sleep(2)
sys.exit(0)
'''


async def _empty() -> AsyncIterator[dict[str, Any]]:
    return
    yield {}  # type: ignore[unreachable]


def _make_transport(cli: Path, ceiling: int | None) -> SubprocessCLITransport:
    """
    Build the real transport pointed at the fake CLI.

    ``args`` carries ``--oversize`` through ``_build_command``'s passthrough so
    the fake CLI learns how big to make the payload without this script having
    to reimplement the command line.
    """
    options = ClaudeAgentOptions(
        cli_path=str(cli),
        max_buffer_size=ceiling,
        extra_args={"oversize": str(OVERSIZE_CHARS)},
    )
    return SubprocessCLITransport(prompt=_empty(), options=options)


async def case_a(cli: Path) -> None:
    """
    Iterate the real transport directly, at the stock 1 MiB ceiling.
    """
    print("\n=== A: transport.read_messages(), ceiling = SDK default (1 MiB) ===")
    transport = _make_transport(cli, ceiling=None)
    await transport.connect()
    print(f"    ceiling in effect: {transport._max_buffer_size} chars")

    stream = transport.read_messages()
    seen: list[str] = []
    raised: BaseException | None = None
    try:
        async for msg in stream:
            seen.append(str(msg.get("marker")))
    except BaseException as exc:  # noqa: BLE001 - the type is the measurement
        raised = exc

    print(f"    markers delivered : {seen}")
    print(f"    exception type    : {type(raised).__name__}")
    print(f"    exception module  : {type(raised).__module__}")
    print(f"    exception str     : {str(raised)[:160]}")

    # The whole resumability question, asked of the generator itself.
    after: list[str] = []
    resume_err: BaseException | None = None
    try:
        async for msg in stream:
            after.append(str(msg.get("marker")))
    except BaseException as exc:  # noqa: BLE001
        resume_err = exc
    print(f"    markers after re-entering the SAME generator: {after}")
    print(f"    re-entry raised   : {type(resume_err).__name__ if resume_err else None}")

    await transport.close()


async def _via_query(cli: Path, ceiling: int | None, label: str) -> None:
    """
    Put the real ``Query`` on top of the real transport and read as a consumer.

    ``initialize()`` is deliberately NOT called: it would need the fake CLI to
    answer a control request, and the handshake is not what is being measured.
    ``start()`` alone spawns the same detached read task that production uses,
    which is the part that decides whether an error ends the stream.
    """
    print(f"\n=== {label} ===")
    transport = _make_transport(cli, ceiling=ceiling)
    await transport.connect()
    print(f"    ceiling in effect: {transport._max_buffer_size} chars")

    query = Query(transport=transport, is_streaming_mode=True)
    await query.start()

    seen: list[str] = []
    raised: BaseException | None = None
    try:
        async for msg in query.receive_messages():
            seen.append(str(msg.get("marker")))
    except BaseException as exc:  # noqa: BLE001 - the type is the measurement
        raised = exc

    print(f"    markers delivered : {seen}")
    print(f"    exception type    : {type(raised).__name__}")
    print(f"    exception module  : {type(raised).__module__}")
    print(f"    exception str     : {str(raised)[:160]}")
    print(f"    is SDKJSONDecodeError? {type(raised).__name__ == 'SDKJSONDecodeError'}")

    after: list[str] = []
    try:
        async for msg in query.receive_messages():
            after.append(str(msg.get("marker")))
    except BaseException as exc:  # noqa: BLE001
        print(f"    re-entry raised   : {type(exc).__name__}")
    print(f"    markers after re-entering receive_messages(): {after}")

    await query.close()


async def main() -> None:
    os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = "1"
    with tempfile.TemporaryDirectory() as td:
        cli = Path(td) / "fake-claude"
        cli.write_text(FAKE_CLI)
        cli.chmod(0o755)

        print(
            f"oversized payload: {OVERSIZE_CHARS} chars "
            f"({OVERSIZE_CHARS / 1024 / 1024:.1f} MiB)"
        )

        await case_a(cli)
        await _via_query(cli, None, "B: Query.receive_messages(), ceiling = SDK default (1 MiB)")
        await _via_query(
            cli,
            OVERSIZE_CHARS * 2,
            "C: Query.receive_messages(), ceiling RAISED above the payload",
        )


if __name__ == "__main__":
    asyncio.run(main())
