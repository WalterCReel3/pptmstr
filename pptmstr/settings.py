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
class RoleChoice:
    """
    What the operator chose for one role of typeset text, by the seam's names.

    ``role`` is a ``ui.typeset.Role`` value (``"prose"``, ``"heading"``, ...), ``slot``
    a ``ui.typeset.Slot`` value (``"proportional"``, ``"monospace"``) and ``step`` a
    size in pixels relative to the body. ``None`` is "the seam's default", so a
    choice that only changes a size does not pin the face as well. Strings, so this
    module stays free of the UI; the seam drops a name it does not know.
    """

    role: str
    slot: str | None = None
    step: int | None = None


LEGACY_FACES: dict[str, tuple[str, tuple[RoleChoice, ...]]] = {
    "roboto": ("roboto", ()),
    "mono": (
        "roboto",
        tuple(RoleChoice(role, "monospace") for role in ("heading", "label", "prose", "table")),
    ),
    "roboto-large": ("roboto", (RoleChoice("prose", "proportional", 2),)),
    "droid": ("droid", ()),
}
"""
What each named face preset of the build before roles means: the family in the
proportional slot, and the roles that differ from the defaults. ``mono`` was every
role in Inconsolata, ``roboto-large`` prose at 18 px beside 16 px chrome.

Spelled here rather than imported from ``ui.typeset`` (its ``_LEGACY``) so this
module stays free of the UI; a test holds the two together.
"""


def legacy_faces(name: str) -> dict[str, Any]:
    """
    The settings a preset's name stands for, as changes for ``Settings.merged``.
    A name that was never a preset is the defaults.
    """
    family, roles = LEGACY_FACES.get(name, LEGACY_FACES["roboto"])
    return {"typeset_family": family, "typeset_roles": roles}


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
    # Set text through the layout library (`ui/typeset.py`) instead of the renderers
    # each pane had before it. Off is not a degraded mode: every pane keeps its old
    # path whole, which is what makes this the instrument for comparing the two.
    typeset: bool = True
    # Which family typeset text's proportional faces are: the name of one of
    # `ui.typeset.PROPORTIONALS`, all from fonts that already ship. Not validated
    # here: the seam owns the names and falls back to its default for one it does
    # not know, so a file written by a later build keeps its choice through this
    # one. The default is spelled rather than imported so this module stays free of
    # the UI; a test holds the two together.
    typeset_family: str = "roboto"
    # The roles of typeset text the operator set apart from the seam's defaults:
    # which of the two live families a role is in, and its size. Only what was
    # chosen, so a default that changes in a later build reaches every role nobody
    # touched. A setting because the question it answers -- does proportional prose
    # beside monospace chrome read as polish or as a foreign body -- is one for
    # whoever is looking at it. Line spacing is deliberately not here: it is a
    # table in the seam (`ui.typeset.LEADING`).
    typeset_roles: tuple[RoleChoice, ...] = ()
    # Which panes set their text through the seam, by `ui.typeset.Pane` value. A
    # pane not named is on; `typeset` above is the switch over all of them.
    typeset_panes: tuple[tuple[str, bool], ...] = ()
    # Typeset text's size as a multiple of the UI font's, which it still follows.
    # The seam clamps it.
    typeset_scale: float = 1.0
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

    def merged(self, **changes: Any) -> Settings:
        return replace(self, **changes)

    def with_role(self, role: str, *, slot: str | None = None, step: int | None = None) -> Settings:
        """
        These settings with one role's choice changed, the rest of it kept: a
        ``slot`` or a ``step`` left out is whatever was chosen before. Kept in
        order of role, as ``load`` reads them, so equal choices are equal settings.
        """
        held = next((each for each in self.typeset_roles if each.role == role), RoleChoice(role))
        made = RoleChoice(
            role, held.slot if slot is None else slot, held.step if step is None else step
        )
        others = tuple(each for each in self.typeset_roles if each.role != role)
        return replace(
            self, typeset_roles=tuple(sorted((*others, made), key=lambda each: each.role))
        )

    def with_pane(self, pane: str, on: bool) -> Settings:
        """
        These settings with one pane switched.
        """
        others = tuple(each for each in self.typeset_panes if each[0] != pane)
        return replace(self, typeset_panes=tuple(sorted((*others, (pane, on)))))


def _connectors(value: Any) -> tuple[McpConnector, ...] | None:
    """
    Decode the persisted connector list, or None when it is not a list at all.

    Entries missing a field or carrying a non-string are dropped rather than
    refused: the rest of the file is still good, and a session that silently loses
    one hand-edited connector is a smaller failure than one that starts with the
    default theme because of it. ``load`` swallows a corrupt file for the same
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


def _roles(value: Any) -> tuple[RoleChoice, ...] | None:
    """
    Decode the persisted role choices -- ``{"prose": {"slot": "monospace", "step":
    2}}`` -- or None when it is not a mapping at all. An entry of the wrong shape
    is dropped and a field of the wrong type is "the default", for the reason
    ``_connectors`` gives.
    """
    if not isinstance(value, dict):
        return None
    out: list[RoleChoice] = []
    for role, choice in value.items():
        if not isinstance(role, str) or not isinstance(choice, dict):
            continue
        slot, step = choice.get("slot"), choice.get("step")
        out.append(
            RoleChoice(
                role,
                slot if isinstance(slot, str) else None,
                step if isinstance(step, int) and not isinstance(step, bool) else None,
            )
        )
    return tuple(sorted(out, key=lambda each: each.role))


def _panes(value: Any) -> tuple[tuple[str, bool], ...] | None:
    """
    Decode the persisted pane switches -- ``{"context": false}`` -- or None when it
    is not a mapping. An entry that is not a name and a boolean is dropped.
    """
    if not isinstance(value, dict):
        return None
    return tuple(
        sorted(
            (pane, on)
            for pane, on in value.items()
            if isinstance(pane, str) and isinstance(on, bool)
        )
    )


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
        if key in _DECODERS:
            decoded = _DECODERS[key](value)
            if decoded is not None:
                clean[key] = decoded
            continue
        default = getattr(Settings(), key)
        if isinstance(default, bool) and not isinstance(value, bool):
            continue
        if isinstance(default, (int, float)) and not isinstance(value, (int, float)):
            continue
        if isinstance(default, str) and not isinstance(value, str):
            continue
        clean[key] = value
    # `typeset_faces` named a preset and `typeset_mono` was the switch before it.
    # A file that says nothing about roles or the family keeps what either said.
    if "typeset_roles" not in clean and "typeset_family" not in clean:
        old = raw.get("typeset_faces")
        if isinstance(old, str):
            clean.update(legacy_faces(old))
        elif raw.get("typeset_mono") is True:
            clean.update(legacy_faces("mono"))
    return Settings(**clean)


_DECODERS: dict[str, Any] = {
    "admitted_connectors": _connectors,
    "typeset_roles": _roles,
    "typeset_panes": _panes,
}
"""
The fields that are not a scalar in the file, each with what reads it back.
"""


def encoded(settings: Settings) -> dict[str, Any]:
    """
    Settings as the file holds them: ``asdict``, with the role choices and the pane
    switches as mappings by name, which is what a person editing the file expects
    and what ``load`` reads.
    """
    out = asdict(settings)
    out["typeset_roles"] = {
        each.role: {
            key: value
            for key, value in (("slot", each.slot), ("step", each.step))
            if value is not None
        }
        for each in settings.typeset_roles
    }
    out["typeset_panes"] = dict(settings.typeset_panes)
    return out


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
                json.dump(encoded(settings), handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError:
        return
