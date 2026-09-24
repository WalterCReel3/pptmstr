"""
The installed Claude Code CLI's version, and whether it clears the sandbox floor.

**Why this exists.** ``planning/2026-09-03-a-dangerously-autonomous-mode.md`` §8c
measured it: an unrecognised settings key is accepted silently -- arm C passed a
nonsense top-level key and the session started normally. A CLI too old to know
``network.strictAllowlist`` or ``failIfUnavailable`` therefore does not complain, it
runs with the key ignored, and the operator watches a session they believe is
contained. §8's conclusion is that ``failIfUnavailable`` cannot protect itself and the
version must be read and refused against a pinned floor before the mode starts.

**The floor is derived, not chosen here.** §8 documents three floors -- ``credentials``
2.1.187, ``filesystem.disabled`` 2.1.216, ``network.strictAllowlist`` 2.1.219 -- and the
configuration in §8 uses ``credentials`` and ``network.strictAllowlist`` but not
``filesystem.disabled``. The floor is the highest of the keys actually used, so 2.1.219.
``SANDBOX_FLOOR_SOURCES`` carries that derivation as data and a test recomputes the
constant from it, so the two cannot drift.

**A gap, recorded rather than guessed.** The record documents no floor for
``failIfUnavailable``, which §8 lists as load-bearing. 2.1.219 is therefore a floor for
the keys whose floors are known, and it is *not* evidence that ``failIfUnavailable`` is
honoured at that version. Finding that floor upstream and raising the constant is
outstanding work.

**Blocking.** ``read_cli_version`` shells out, so this module sits on the same side of
the boundary as ``sessions.py`` and ``tree.py``: nothing here may be called from the
reducer or a draw call. The split is the same one those modules make -- the blocking
lives behind one name (``read_cli_version``) and the judgement (``check_floor``) is
pure, so the interesting cases are testable without a subprocess.

**Nothing here raises, and unreadable is not the same answer as too old.** The SDK's own
``_check_claude_version`` logs a warning inside ``except Exception: pass``, which §8
cites as exactly the behaviour not to copy: a version it could not read and a version
that is fine are indistinguishable afterwards. ``FloorCheck`` has a third arm so the
caller can refuse the mode on "could not tell" as readily as on "too old".

**The instrument is ``--version``**, matching ``scripts/probe.py:check_agent_cli``,
rather than the SDK's ``-v``. The observed output is ``2.1.251 (Claude Code)``; no CLI
older than the floor has been run against this parser, so the refusal path is exercised
only by the stand-in binaries in ``tests/test_cli_version.py``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "SANDBOX_FLOOR",
    "SANDBOX_FLOOR_SOURCES",
    "FloorCheck",
    "MeetsFloor",
    "BelowFloor",
    "Unreadable",
    "VersionRead",
    "check_floor",
    "check_installed_cli",
    "parse_version",
    "read_cli_version",
    "resolve_cli_path",
]


# Keyed by the settings key that needs it, from §8's "Three things this configuration
# does not do for itself". Only keys the §8 configuration actually sets belong here:
# `filesystem.disabled` has a documented floor of 2.1.216 and is deliberately absent
# because the configuration does not use it, and a floor for a key nobody sets would
# refuse launches for no gain.
SANDBOX_FLOOR_SOURCES: dict[str, str] = {
    "credentials": "2.1.187",
    "network.strictAllowlist": "2.1.219",
}

# The highest floor among the keys in use. Spelled as a literal rather than computed at
# import so it is greppable and reviewable; tests/test_cli_version.py recomputes it from
# SANDBOX_FLOOR_SOURCES and fails if the two disagree.
SANDBOX_FLOOR = "2.1.219"

# `claude --version` prints `2.1.251 (Claude Code)`: a dotted numeric run, then a
# product name. Anchored at the start so a version embedded in a later word cannot be
# mistaken for the version itself.
_VERSION_HEAD = re.compile(r"^\s*(\d+(?:\.\d+)*)")

# The SDK's `_check_claude_version` allows 2s. This is generous by comparison because it
# runs once at launch rather than per session, and a cold-cache first execution of a
# large bundled binary is the case that would otherwise read as "unreadable" on a
# perfectly good install.
_DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class VersionRead:
    """
    What asking the CLI for its version produced.

    ``text`` is the raw first line of stdout when the process ran and exited zero, and
    ``None`` when nothing usable came back. ``detail`` always says what happened and is
    the string an operator-facing refusal quotes -- it is populated on the success path
    too, naming the binary that answered, because "which claude did you ask?" is the
    first question a surprising version raises.
    """

    text: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class MeetsFloor:
    """
    The CLI is at or above the floor. ``components`` is the parsed version.
    """

    version: str
    components: tuple[int, ...]
    floor: str


@dataclass(frozen=True, slots=True)
class BelowFloor:
    """
    The CLI ran and is too old for the sandbox keys the mode sets.
    """

    version: str
    components: tuple[int, ...]
    floor: str


@dataclass(frozen=True, slots=True)
class Unreadable:
    """
    No version could be established. ``detail`` carries why, for the refusal message.

    Distinct from ``BelowFloor`` on purpose: one says the installed CLI is known to be
    too old, the other says containment cannot be confirmed either way. Both refuse the
    mode, and an operator needs different actions for them.
    """

    detail: str
    floor: str


FloorCheck = MeetsFloor | BelowFloor | Unreadable


def parse_version(text: str) -> tuple[int, ...] | None:
    """
    The leading dotted numeric run of a version string, or ``None`` if there is none.

    Components are integers so comparison is numeric: 2.1.9 is older than 2.1.187 and a
    string comparison says the opposite.

    A trailing non-numeric suffix is dropped rather than rejected, so ``2.1.219-rc.1``
    parses as ``(2, 1, 219)`` -- which treats a pre-release as its own release and is
    the permissive direction. The CLI does not currently print such a version; if it
    starts to, this is the line that has to decide what a pre-release means.
    """
    match = _VERSION_HEAD.match(text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _padded(
    left: tuple[int, ...], right: tuple[int, ...]
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    width = max(len(left), len(right))
    return (
        left + (0,) * (width - len(left)),
        right + (0,) * (width - len(right)),
    )


def resolve_cli_path() -> str | None:
    """
    The CLI binary the SDK would spawn, or ``None`` if there is not one.

    The SDK prefers the copy bundled in its own wheel and only falls back to ``PATH``
    (``subprocess_cli.py``: ``_find_cli``). Resolving it the same way here, rather than
    running whatever ``claude`` is on ``PATH``, is the difference between reading the
    version of the binary that will be used and reading a different one that happens to
    share a name -- the same reasoning ``scripts/probe.py:check_agent_cli`` records, and
    it bites harder here because a stale ``PATH`` copy could clear the floor while the
    bundled one that actually launches does not.

    The SDK's ``_find_cli`` is private and raises rather than returning, so it is not
    called directly; this reproduces its first two steps only. A machine where the CLI
    is found solely by one of ``_find_cli``'s later fixed-location probes reads as not
    found here, which surfaces as ``Unreadable`` and refuses the mode. That is the safe
    direction for a containment check.
    """
    name = "claude.exe" if sys.platform == "win32" else "claude"
    try:
        import claude_agent_sdk
    except ImportError:
        return shutil.which(name)

    package = claude_agent_sdk.__file__
    if package is not None:
        bundled = Path(package).parent / "_bundled" / name
        if bundled.exists() and bundled.is_file():
            return str(bundled)
    return shutil.which(name)


def read_cli_version(path: str | None = None, *, timeout: float = _DEFAULT_TIMEOUT) -> VersionRead:
    """
    Run ``<claude> --version`` and return what came back. **Blocking.**

    ``path`` defaults to ``resolve_cli_path()``. Every failure mode -- no binary, not
    executable, a timeout, a non-zero exit, empty output, output that is not valid text
    -- returns a ``VersionRead`` with ``text=None`` and a ``detail`` saying which, or in
    the last case a reading with the bad bytes replaced. Nothing raises, because the
    caller is a launch path that has to refuse with a reason rather than crash the UI.
    """
    target = path if path is not None else resolve_cli_path()
    if target is None:
        return VersionRead(None, "the claude CLI is not bundled with the SDK and not on PATH")

    # The codec is named because `text=True` alone decodes strictly under the launcher's
    # locale, and UnicodeDecodeError is a ValueError -- neither handler below catches it.
    # `replace` rather than tree.py's `surrogateescape`: nothing here returns to the
    # filesystem, and a lone surrogate would only move the raise to whatever renders
    # `detail`.
    try:
        proc = subprocess.run(
            [target, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return VersionRead(None, f"{target} --version did not answer within {timeout:g}s")
    except (subprocess.SubprocessError, OSError) as exc:
        # FileNotFoundError for a path that does not exist, PermissionError for one
        # that is not executable, and OSError generally for a binary this kernel
        # cannot run at all.
        return VersionRead(None, f"{target} failed to run: {type(exc).__name__}: {exc}")

    if proc.returncode != 0:
        noise = (proc.stdout + proc.stderr).strip().splitlines()
        first = noise[0] if noise else "(no output)"
        return VersionRead(None, f"{target} --version exited {proc.returncode}: {first}")

    # First line only: a CLI that prints an update notice after the version would
    # otherwise hand the parser a blob whose leading token is not the version.
    head = proc.stdout.strip().splitlines()
    if not head:
        return VersionRead(None, f"{target} --version exited 0 but printed nothing")
    return VersionRead(head[0].strip(), f"{target} reported {head[0].strip()!r}")


def check_floor(read: VersionRead, floor: str = SANDBOX_FLOOR) -> FloorCheck:
    """
    Judge a reading against a floor. Pure; no IO, no clock.

    An unparseable ``floor`` yields ``Unreadable`` rather than raising, so a bad
    constant cannot take a launch down with a traceback -- it refuses the mode, which is
    the same outcome every other failure here produces.
    """
    floor_components = parse_version(floor)
    if floor_components is None:
        return Unreadable(f"the pinned floor {floor!r} is not a version", floor)

    if read.text is None:
        return Unreadable(read.detail, floor)

    components = parse_version(read.text)
    if components is None:
        return Unreadable(f"{read.detail}, which is not a version", floor)

    left, right = _padded(components, floor_components)
    if left < right:
        return BelowFloor(read.text, components, floor)
    return MeetsFloor(read.text, components, floor)


def check_installed_cli(
    path: str | None = None,
    *,
    floor: str = SANDBOX_FLOOR,
    timeout: float = _DEFAULT_TIMEOUT,
) -> FloorCheck:
    """
    Read the installed CLI's version and judge it. **Blocking.**

    The composition a launch path wants: one call, one value to ``match`` on, and no
    re-parsing to decide what to tell the operator.

    **Pass ``path`` when the launch sets ``ClaudeAgentOptions.cli_path``.** That option
    overrides the SDK's own resolution, so leaving ``path`` at ``None`` would measure
    ``resolve_cli_path()``'s answer while the session spawns a different binary --
    exactly the mismatch this module exists to avoid.
    """
    return check_floor(read_cli_version(path, timeout=timeout), floor)
