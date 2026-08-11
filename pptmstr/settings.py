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
from pathlib import Path
from typing import Any

APP_NAME = "pptmstr"


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything that must survive a restart."""

    theme: str = "dark"
    # Subprocess-bound rather than GIL-bound: one CLI process per session, so the
    # ceiling is RAM. Surfaced and configurable rather than hard-coded (design §9).
    concurrency_cap: int = 4
    # Full speed while any agent is active, this while everything is idle or parked.
    fps_idle: float = 9.0
    # Word-wrap the multi-line composers instead of scrolling them horizontally.
    # Persisted rather than per-pane because it is a property of how the operator
    # reads, not of any one composer -- the reply box and the argument editor
    # disagreeing about it would be a defect, not a feature. Upstream still marks
    # wrapping BETA (caret and selection across wrapped rows are the under-tested
    # part), which is the reason this is a setting at all and not just the default.
    wrap_inputs: bool = True

    def merged(self, **changes: Any) -> Settings:
        return replace(self, **changes)


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


def load(path: Path | None = None) -> Settings:
    """
    Read settings, falling back to defaults on anything unreadable.

    A corrupt or hand-edited config must not stop the app from starting -- losing a
    theme preference is a nuisance, refusing to launch over it is a bug. Unknown
    keys are ignored rather than rejected so that a file written by a later version
    still loads here.
    """
    target = path or settings_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(raw, dict):
        return Settings()

    known = {f.name: f.type for f in fields(Settings)}
    clean: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in known:
            continue
        # Guard the types the constructor would otherwise accept silently: a string
        # in fps_idle would survive construction and fail much later, inside a
        # comparison in the frame loop.
        default = getattr(Settings(), key)
        if isinstance(default, bool) and not isinstance(value, bool):
            continue
        if isinstance(default, (int, float)) and not isinstance(value, (int, float)):
            continue
        if isinstance(default, str) and not isinstance(value, str):
            continue
        clean[key] = value
    return Settings(**clean)


def save(settings: Settings, path: Path | None = None) -> None:
    """
    Write settings atomically.

    Written to a temporary file in the same directory and renamed over the target,
    so an interrupted write leaves the previous file intact instead of a truncated
    one. rename(2) is atomic within a filesystem, which is why the temp file cannot
    just go in /tmp.

    Failures are swallowed: this is called from the UI thread on a theme change, and
    a read-only config directory must not take down a frame.
    """
    target = path or settings_path()
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
        return
