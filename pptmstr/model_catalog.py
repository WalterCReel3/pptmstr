"""
The models the launcher offers, and where that list comes from.

**The bundled CLI says, and nothing here asks the API.** pptmstr makes no model request
of its own: every one goes through the bundled Claude Code CLI, which holds the
operator's sign-in. The CLI reports the models it offers in the SDK's initialize
handshake, so ``probe_handshake`` connects once, reads ``get_server_info()`` and
disconnects -- no prompt is sent and no credential is read. ``GET /v1/models`` would need
a credential in pptmstr's hands, and pptmstr reads none.

**``MODELS`` is the list until the handshake answers, and when it never does.** The
probe is an override, not a dependency: it runs on a worker after the first frame, and a
CLI that is missing, slow, signed out or answering in a shape this module does not read
leaves ``MODELS`` on offer with the reason in the log. ``--model`` and a pinned pick reach
the session as typed either way (``launcher.LauncherState`` holds a name, not an index),
so a model neither list carries is reachable, only not listed.

**Concrete ids, not aliases**, so that what the combo shows is what the session runs.
The handshake names each model twice: ``value`` is the CLI's alias (``opus``,
``default``) and ``resolvedModel`` the id that alias means today. The id is what is
offered, what a draft holds and what ``--model`` is handed. An alias follows Anthropic's
mapping, so a draft saved on ``opus`` would resume on whatever ``opus`` means next month
-- at that model's price -- and the session's record would not say which model ran.

**The default is a name, not a position.** ``DEFAULT_MODEL`` is spelled separately from
``MODELS`` so neither reordering the list nor the handshake's own order can move the
default -- and its price. A default the handshake does not list is still offered: the
launcher prepends a pick its list lacks.

**What the handshake carries is not documented.** ``get_server_info`` is; the
``models`` key inside it is not. The parser therefore refuses entries one at a time
rather than the whole list, and a list with no usable entry is a failed probe.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, assert_never

from .log import LOG

__all__ = [
    "DEFAULT_MODEL",
    "MODELS",
    "Catalog",
    "CatalogProbe",
    "Probed",
    "Unavailable",
    "catalog",
    "from_handshake",
    "probe_handshake",
]


# The four ids the CLI's handshake listed, in its order (measured against CLI 2.1.300),
# so that a failed probe offers what a successful one would have.
MODELS: tuple[str, ...] = (
    "claude-opus-5-5",
    "claude-fable-5-1",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
)

# The selection a launch gets when nobody picks one -- opus-5-5 by the operator's choice
# (2026-10-18). A name and not ``MODELS[0]``: see the module docstring. Same reasoning as
# ``launcher.LauncherState.policy`` holding a ``Policy`` rather than an index.
DEFAULT_MODEL = "claude-opus-5-5"

# Long enough for a cold connect -- measured at 6.5 s and 8.2 s against CLI 2.1.300 --
# and short because a hung CLI holds a worker thread, and ``MODELS`` is already a usable
# answer.
_TIMEOUT_S = 30.0


@dataclass(frozen=True, slots=True)
class Probed:
    """
    The handshake listed at least one model, in its own order, each id once.

    ``refused`` counts the entries that named no usable id, so a list that shrank
    because the handshake's shape moved says so in the log rather than looking short.
    """

    models: tuple[str, ...]
    refused: int = 0


@dataclass(frozen=True, slots=True)
class Unavailable:
    """
    No list could be established. ``detail`` says why, for the log.

    One arm rather than several because every cause has the same consequence here --
    ``MODELS`` stands in -- and none asks anything of the operator.
    """

    detail: str


CatalogProbe = Probed | Unavailable


def _model_id(entry: object) -> str | None:
    """
    The concrete id one handshake entry names, or ``None`` when it names none.

    ``resolvedModel`` and never ``value``: ``value`` is an alias where one exists, and
    falling back to it would offer an alias under the guise of an id.
    """
    if not isinstance(entry, dict):
        return None
    resolved = entry.get("resolvedModel")
    if not isinstance(resolved, str) or not resolved.strip():
        return None
    return resolved.strip()


def from_handshake(info: object) -> CatalogProbe:
    """
    The models a ``get_server_info()`` answer lists. Pure.

    Entries are refused one by one: a malformed entry costs that entry, not the list.
    An id two aliases resolve to (``default`` and ``opus`` both name the same model) is
    listed once, where it first appears. Keys this does not read are ignored.
    """
    if not isinstance(info, dict):
        return Unavailable("the handshake answered with no server info")
    if "models" not in info:
        return Unavailable("the handshake carried no models list")
    entries = info["models"]
    if not isinstance(entries, list):
        return Unavailable(f"the handshake's models is a {type(entries).__name__}, not a list")

    found: list[str] = []
    refused = 0
    for entry in entries:
        model = _model_id(entry)
        if model is None:
            refused += 1
        elif model not in found:
            found.append(model)

    if not found:
        return Unavailable(
            f"the handshake listed no usable model ({len(entries)} entries, {refused} refused)"
        )
    return Probed(tuple(found), refused)


def catalog(probe: CatalogProbe, fallback: tuple[str, ...] = MODELS) -> tuple[str, ...]:
    """
    The list to offer, given what the probe produced. Pure.

    ``Probed`` with an empty tuple cannot come out of ``from_handshake`` and is handled
    anyway, because a caller constructing one by hand should still get a list a launcher
    can draw.
    """
    match probe:
        case Probed(models):
            return models or fallback
        case Unavailable():
            return fallback
        case _:
            assert_never(probe)


async def _server_info(timeout: float) -> dict[str, Any] | None:
    # Imported here so that importing this module -- which the launcher does -- does not
    # import the SDK. The probe only ever runs on a worker.
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    options = ClaudeAgentOptions(
        # The driver's reason (``AgentSession._options``): without it the CLI also starts
        # every stdio server the operator's ``.mcp.json``, settings and plugins name, and
        # it starts them before anything could refuse one. This connect needs none.
        strict_mcp_config=True,
        # Piped and dropped. Without a callback the CLI's diagnostics go to the terminal
        # pptmstr was started from, and a failure here is reported by its exception.
        stderr=lambda _line: None,
    )
    # Setting sources are left at the CLI's default, as every session's are: a settings
    # file can narrow or redirect the models offered, and the list should be the one a
    # session started now would be given. A SessionStart hook in project settings did not
    # fire on this connect (measured, CLI 2.1.300): no prompt means no session start.
    client = ClaudeSDKClient(options=options)

    async def ask() -> dict[str, Any] | None:
        # ``connect`` disconnects itself on any failure, cancellation by the timeout
        # included, so only a connect that succeeded needs the ``finally``.
        await client.connect()
        try:
            return await client.get_server_info()
        finally:
            await client.disconnect()

    return await asyncio.wait_for(ask(), timeout)


def probe_handshake(timeout: float = _TIMEOUT_S) -> CatalogProbe:
    """
    Ask the bundled CLI which models it offers. **Blocking**: it starts the CLI and waits
    for its handshake. Never raises.
    """
    try:
        info = asyncio.run(_server_info(timeout))
    except TimeoutError:
        return Unavailable(f"the CLI did not finish its handshake within {timeout:g}s")
    except Exception as exc:  # noqa: BLE001 - reported as a reason, not swallowed
        return Unavailable(f"{type(exc).__name__}: {exc}")
    return from_handshake(info)


@dataclass
class Catalog:
    """
    The offerable list, refreshed once per process on a worker.

    ``models`` is readable from the first frame and is ``MODELS`` until a probe replaces
    it, so nothing waits and no caller handles "not answered yet".

    ``probe`` is ``None`` unless the process start arms it (``app._start``), so a state
    built anywhere else -- a test, the fake driver -- never starts the CLI.

    **Once per process, not once per launch**, for the reason ``launcher.VersionGate``
    records: re-probing per modal would start a CLI behind a keystroke.

    Mutable and unsynchronised by design: ``poll`` is called from the draw and is the
    only writer of ``models``. The worker touches nothing but the queue.
    """

    probe: Callable[[], CatalogProbe] | None = None
    models: tuple[str, ...] = MODELS
    detail: str | None = None
    answered: bool = False
    probing: bool = False
    _inbox: queue.SimpleQueue[CatalogProbe] = field(default_factory=queue.SimpleQueue, repr=False)

    def request(self) -> threading.Thread | None:
        """
        Start the probe on a worker unless there is none or one has already run.
        Returns without waiting; the worker is returned so a test can join it.
        """
        if self.probe is None or self.answered or self.probing:
            return None
        self.probing = True
        inbox, probe = self._inbox, self.probe

        def work() -> None:
            try:
                inbox.put(probe())
            except Exception as exc:  # noqa: BLE001 - a probe documents no raise
                # Caught at the thread boundary anyway: an exception escaping here would
                # leave `probing` true forever and nothing in the log to say why.
                inbox.put(Unavailable(f"{type(exc).__name__}: {exc}"))

        worker = threading.Thread(target=work, name="pptmstr-model-catalog", daemon=True)
        worker.start()
        return worker

    def poll(self) -> None:
        """
        Take the worker's answer if it has arrived, and say what it was in the log.
        Cheap enough to call every frame.
        """
        if self.answered:
            return
        try:
            probe = self._inbox.get_nowait()
        except queue.Empty:
            return
        self.models = catalog(probe)
        self.answered = True
        self.probing = False
        match probe:
            case Probed(models, refused):
                self.detail = None
                skipped = f"; {refused} entries named no model and were skipped" if refused else ""
                LOG.info("models", f"the CLI offers {len(models)} model(s){skipped}")
            case Unavailable(detail):
                self.detail = detail
                LOG.warn("models", f"offering the list in code: {detail}")
            case _:
                assert_never(probe)
