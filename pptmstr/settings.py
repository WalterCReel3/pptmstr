"""
Persisted operator preferences.

Lives under ``XDG_CONFIG_HOME`` (falling back to ``~/.config``) rather than beside
the source. A repo-relative path works for as long as every launch happens from a
source tree and then breaks silently once installed as a wheel, which is the worst
possible time to find out.

Deliberately separate from hello_imgui's own ini. That file is hello_imgui's format
for hello_imgui's concerns -- docking layout, window geometry -- and it is rewritten
by the runner on its own schedule. This is ours: theme now, concurrency cap and
per-node trust lists later.

Not part of the store. These are properties of the operator, not of the world, so
they never enter a Snapshot (I1).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields, replace
from enum import Enum
from pathlib import Path
from typing import Any

APP_NAME = "pptmstr"


@dataclass(frozen=True, slots=True)
class McpConnector:
    """
    A claude.ai connector the operator has admitted into every session.

    Plain data, and deliberately free of SDK imports: the CLI's wire shape for one
    of these is ``{"type": "claudeai-proxy", "url": ..., "id": ...}``, and building
    that dict is ``driver``'s job. What is persisted is the operator's decision, not
    a transport detail.

    ``id`` is the account's ``mcpsrv_`` identifier. It is not re-validated at launch:
    a rotated id means the CLI does not connect that server and the operator re-runs
    `scripts/verify_mcp_status.py`. Checking it on every launch would put a network
    fetch of every connector in front of every session to catch a rare case.
    """

    name: str
    url: str
    id: str


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything that must survive a restart."""

    theme: str = "dark"
    # Subprocess-bound rather than GIL-bound: one CLI process per session, so the
    # ceiling is RAM. Surfaced and configurable rather than hard-coded (design §9).
    concurrency_cap: int = 4
    # Sub-agents of one session, which is a different constraint from the line above
    # and must not be sized from it: a sub-agent is not a subprocess, it shares its
    # parent's CLI process, so the cost is concurrent API streams and token burn
    # rather than RAM. Kept equal to driver.DEFAULT_SUBAGENT_CAP by a test, which is
    # where the argument for the number lives.
    #
    # The floor under a launch and not the last word on one: `LaunchSpec.subagent_cap`
    # overrides it for a single session, because capacity is the kind of thing an
    # operator sizes per piece of work rather than once.
    subagent_cap: int = 8
    # Full speed while any agent is active, this while everything is idle or parked.
    fps_idle: float = 9.0
    # Word-wrap the multi-line composers instead of scrolling them horizontally.
    # Persisted rather than per-pane because it is a property of how the operator
    # reads, not of any one composer -- the reply box and the argument editor
    # disagreeing about it would be a defect, not a feature. Upstream still marks
    # wrapping BETA (caret and selection across wrapped rows are the under-tested
    # part), which is the reason this is a setting at all and not just the default.
    wrap_inputs: bool = True
    # The MCP servers a session may reach, and the whole of them. `driver` passes
    # these alongside the in-process bus under `strict_mcp_config=True`, so a server
    # the operator has not named here is one no session loads -- which is what keeps
    # an stdio server out of a process the sandbox does not wrap (`sandbox.py`: "the
    # CLI process runs outside the sandbox").
    #
    # Admission is not a widening of the gate. Every `mcp__*` call still reaches
    # `approval.classify`'s fail-closed fallthrough and parks in front of the
    # operator at every policy, `AUTONOMOUS` included.
    admitted_connectors: tuple[McpConnector, ...] = ()
    # Whether a team session's board is written to disk as it changes and read
    # back when the session is resumed (``boardsync``).
    #
    # On, a session's log is the files of ``config_dir() / "boards" / <session
    # id>``: every specification, result and concern body its board has held, as
    # plain text. **Nothing in pptmstr ever deletes one**, so they outlive the
    # CLI's own transcript of the session, which the CLI removes on a retention
    # schedule this does not follow. Off, nothing is read, written or created.
    #
    # Read once, when the application starts, and edited only in this file: a
    # log begun part-way through a session would restore as a board missing
    # everything before the switch with nothing in it to say so.
    #
    # The one key ``read`` does not default when it cannot tell what the file
    # says (``BoardLogUnread``): on is the default only for a file or a key that
    # is not there.
    board_log: bool = True
    # Whether the fleet folds an ended sub-agent that owes nothing and holds nothing
    # into a count on its lead's card. A filter on what is drawn: the record stays in
    # the store either way.
    #
    # Read like every key but ``board_log``: a value that is not a JSON ``true`` or
    # ``false`` is the default, and nothing is refused over it.
    hide_ended_subagents: bool = True

    def merged(self, **changes: Any) -> Settings:
        return replace(self, **changes)


def _connectors(value: Any) -> tuple[McpConnector, ...] | None:
    """
    Decode the persisted connector list, or None when it is not a list at all.

    Entries missing a field or carrying a non-string are dropped rather than
    refused: the rest of the file is still good, and a session that silently loses
    one hand-edited connector is a smaller failure than one that starts with the
    default theme because of it. ``read`` swallows a corrupt file for the same
    reason.
    """
    if not isinstance(value, list):
        return None
    out: list[McpConnector] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name, url, ident = item.get("name"), item.get("url"), item.get("id")
        if isinstance(name, str) and isinstance(url, str) and isinstance(ident, str):
            out.append(McpConnector(name=name, url=url, id=ident))
    return tuple(out)


def config_dir() -> Path:
    """
    The directory settings live in. Does not create it.

    XDG_CONFIG_HOME is only honoured when absolute, per the spec -- a relative value
    is required to be treated as unset, and honouring it anyway would scatter config
    into whatever the working directory happened to be.
    """
    raw = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(raw) if raw.startswith("/") else Path.home() / ".config"
    return base / APP_NAME


def settings_path() -> Path:
    return config_dir() / "settings.json"


class BoardLogUnread(Enum):
    """
    Why ``board_log`` is off although the file does not say ``false``: something
    was there and what it says about recording could not be read.

    Each value is the reason as the board pane gives it after "not recorded:".
    Three, because they are different mistakes with different repairs -- a file
    that is not JSON, one that is and spells the switch's value as something
    other than a bool, and one that spells the switch's name wrong.
    """

    FILE = "settings could not be read"
    VALUE = "board_log in settings.json is not true or false"
    KEY = "settings.json has a key that is board_log misspelled"


def _misspells_board_log(key: str) -> bool:
    """
    Whether ``key`` is not ``board_log`` and has its letters: the same once case
    and everything that is not a letter or a digit are taken out.

    ``board-log``, ``boardlog``, ``Board_Log`` and ``board_log`` with a space
    after it. The switch is set by hand and by nothing else, so a key like this
    is an operator who meant it. Nothing further off is guessed at -- a dropped
    letter, another word -- and such a file records, which is the part of this
    that cannot be closed from here.
    """
    return key != "board_log" and "".join(c for c in key.lower() if c.isalnum()) == "boardlog"


@dataclass(frozen=True, slots=True)
class Loaded:
    """
    What ``read`` found: the settings to run with, and why ``board_log`` is off
    when that is not because the file says so.

    The reason rides beside ``Settings`` and not in it. ``save`` writes every
    field of ``Settings``, and a reason written back would be read by the next
    start as a key somebody set.
    """

    settings: Settings
    board_log_unread: BoardLogUnread | None = None


def read(path: Path | None = None) -> Loaded:
    """
    Read settings, falling back to defaults on anything unreadable -- for every
    key but ``board_log``.

    A corrupt or hand-edited config must not stop the app from starting -- losing a
    theme preference is a nuisance, refusing to launch over it is a bug. Unknown
    keys are ignored rather than rejected so that a file written by a later version
    still loads here.

    **``board_log`` is the exception, and it fails the other way.** Its default
    is on, it has no menu entry, and switching it off is a hand edit of this
    file. Defaulting it would turn a trailing comma, or ``"false"`` typed with
    quotes, into a board written to disk for an operator who asked for none; for
    every other key the fallback loses a preference, and for this one it would
    reverse a choice about what is kept. So the default applies to exactly two
    findings -- no file, and a file with no such key -- and anything else that
    is not a JSON ``true`` or ``false`` is off for this run, with the reason in
    ``Loaded.board_log_unread``. "No such key" does not cover the key
    misspelled (``_misspells_board_log``): that is off too, whatever it is set
    to and whatever a correctly spelled key beside it says.

    The cost is the operator who never touched the switch and broke the file
    some other way: nothing is recorded for that run either. It stops at the
    run, because ``save`` refuses to write these values back (``Unsaved``). That
    is the cheaper of the two mistakes -- a board not kept can be switched back
    on, and one written cannot be unwritten by the program that never deletes
    one.
    """
    target = path or settings_path()
    off = Settings(board_log=False)
    try:
        text = target.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        # Nothing is there, which is what a first launch finds.
        return Loaded(Settings())
    except (OSError, ValueError):
        # Something is there and it cannot be opened, or is not text.
        return Loaded(off, BoardLogUnread.FILE)
    try:
        raw = json.loads(text)
    except ValueError:
        return Loaded(off, BoardLogUnread.FILE)
    if not isinstance(raw, dict):
        return Loaded(off, BoardLogUnread.FILE)

    known = {f.name: f.type for f in fields(Settings)}
    clean: dict[str, Any] = {}
    unread: BoardLogUnread | None = None
    misspelled = False
    for key, value in raw.items():
        if key not in known:
            misspelled = misspelled or _misspells_board_log(key)
            continue
        # Guard the types the constructor would otherwise accept silently: a string
        # in fps_idle would survive construction and fail much later, inside a
        # comparison in the frame loop.
        if key == "admitted_connectors":
            decoded = _connectors(value)
            if decoded is not None:
                clean[key] = decoded
            continue
        if key == "board_log" and not isinstance(value, bool):
            # Not skipped like the other keys: skipping is the default, and the
            # default is on. No spelling is guessed at either way -- "true" is
            # refused with "false", since a rule for which strings mean on is a
            # rule a string can satisfy by accident.
            clean[key], unread = False, BoardLogUnread.VALUE
            continue
        default = getattr(Settings(), key)
        if isinstance(default, bool) and not isinstance(value, bool):
            continue
        if isinstance(default, (int, float)) and not isinstance(value, (int, float)):
            continue
        if isinstance(default, str) and not isinstance(value, str):
            continue
        clean[key] = value
    if misspelled:
        # After the loop, so the answer does not turn on which key the file
        # lists first. A value that could not be read keeps its own reason.
        clean["board_log"], unread = False, unread or BoardLogUnread.KEY
    return Loaded(Settings(**clean), unread)


def load(path: Path | None = None) -> Settings:
    """
    ``read`` without the reason, for a caller that only needs the values.

    The values decide on their own: ``board_log`` is already off wherever
    ``read`` could not tell what the file says about it, so a caller that never
    asks why still records nothing.
    """
    return read(path).settings


@dataclass(frozen=True, slots=True)
class Unsaved:
    """
    A ``save`` that was refused: nothing was written, and the file at ``path``
    is as it was.

    ``why`` is what could not be read -- of the file there now, or of the one
    the run started from.
    """

    path: Path
    why: BoardLogUnread

    @property
    def told(self) -> str:
        """
        The refusal as the operator is told it, once: which file, what is wrong
        with it, and what ends it.
        """
        return (
            f"settings are not being saved: {self.why.value} ({self.path}). The file is"
            " left as it is, and what you change here lasts for this run only -- repair"
            " the file and restart"
        )


def save(
    settings: Settings, path: Path | None = None, *, unread: BoardLogUnread | None
) -> Unsaved | None:
    """
    Write settings atomically, or refuse and say why. None is not a refusal.

    Written to a temporary file in the same directory and renamed over the target,
    so an interrupted write leaves the previous file intact instead of a truncated
    one. rename(2) is atomic within a filesystem, which is why the temp file cannot
    just go in /tmp.

    **Refused in two cases, and both are about ``board_log``.** The file is
    written whole from ``settings``, so a save is the one place a value nobody
    chose could become a value the file states.

    - ``unread`` is ``Loaded.board_log_unread`` of the read ``settings`` came
      from. When it is not None the ``board_log`` in hand is ``read``'s fail-safe
      and not anything the file said, and writing it would switch recording off
      for good on the strength of a typo. It has no default: a caller that left
      it out would be claiming the values were read cleanly.
    - The file there now is read again, and refused the same way if it cannot
      be told what it says. It may have been broken, or hand-edited to something
      that is not a bool, since the run started; either is somebody's text this
      would replace with a file that parses and says something else.

    A refusal for the first reason lasts the run: repairing the file does not
    make the values in memory the operator's.

    Failures of the write itself are swallowed: this is called from the UI thread
    on a theme change, and a read-only config directory must not take down a frame.
    """
    target = path or settings_path()
    why = unread or read(target).board_log_unread
    if why is not None:
        return Unsaved(target, why)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".settings-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(asdict(settings), handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError:
        pass
    return None
