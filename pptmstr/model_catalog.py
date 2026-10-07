"""
The models the launcher offers, and where that list comes from.

**Why a probe at all.** The list used to be a tuple in ``ui/launcher.py`` carrying the
comment "verified against the model-config docs at build time". That is true only until
the next model ships: a build-time check cannot see a model released after the build, so
the operator's list goes stale silently and the newest model is unreachable from the UI
until someone edits the tuple. ``GET /v1/models`` answers the same question at runtime.

**The list in code did not go away.** ``FALLBACK_MODELS`` is what the launcher offers
when the probe has not answered yet, could not answer, or answered with nothing. The
probe is an override, not a dependency -- a machine with no network still launches.

**The probe cannot supply aliases.** ``/v1/models`` returns concrete ids only; there is
no ``opus`` or ``sonnet`` in the response. Offering aliases would therefore mean a second
list that no probe can ever refresh, which is the problem this module exists to remove,
so the launcher offers concrete ids and ``DEFAULT_MODEL`` names one.

**Ordering is the API's.** The response is newest-first, which is the order an operator
scanning for "the model that just shipped" wants. Nothing here sorts it. The default is
a *name* rather than the first entry precisely so that this ordering -- which is chosen
elsewhere and can change without notice -- cannot move it.

**Blocking, and nothing here raises.** ``probe_catalog`` reads a file and makes a network
call, so this module sits where ``cli_version.py`` sits: it may not be called from the
reducer or a draw. The split is the same one -- the blocking lives behind one name and
the judgements (``is_expired``, ``catalog``) are pure, so the interesting cases are
testable without a socket. Every failure returns ``Unavailable`` with a reason.

**Credentials.** There is no ``ANTHROPIC_API_KEY`` in a normal pptmstr install: auth is
the bundled Claude Code CLI's, stored as an OAuth token in ``~/.claude/.credentials.json``.
So the zero-argument ``anthropic.Anthropic()`` raises here -- it resolves env vars and
``ant`` profiles, and this machine has neither -- and the token is passed explicitly as
``auth_token``, which puts it on ``Authorization: Bearer`` where an OAuth token belongs.
Refreshing that token is Claude Code's job and not this module's; an expired one is a
failed probe and the list in code stands in.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import assert_never

__all__ = [
    "CREDENTIAL_PATH",
    "DEFAULT_MODEL",
    "FALLBACK_MODELS",
    "Catalog",
    "CatalogProbe",
    "Credential",
    "Probed",
    "Unavailable",
    "catalog",
    "is_expired",
    "parse_credential",
    "probe_catalog",
    "read_credential",
]


# What the launcher offers before the probe answers, and after it fails. Ordered
# newest-first to match what a successful probe returns, so the list does not visibly
# reshuffle when one arrives.
FALLBACK_MODELS: tuple[str, ...] = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-haiku-4-5-20251001",
)

# The selection a launch gets when nobody picks one. A name and not ``FALLBACK_MODELS[0]``
# or ``models[0]``: the probed order is the API's, so a position would hand the choice of
# default -- and its price -- to whichever model shipped most recently. Same reasoning as
# ``launcher.LauncherState.policy`` holding a ``Policy`` rather than an index.
DEFAULT_MODEL = "claude-sonnet-5"

# Left unexpanded for the same reason ``sandbox.DENIED_CREDENTIAL_FILES`` leaves it so:
# expanding it here would put an environment read inside a module-level constant.
# ``read_credential`` expands it.
CREDENTIAL_PATH = "~/.claude/.credentials.json"

# Short on purpose. The probe runs on a worker while the UI is already up, so a slow
# answer costs nothing but a late list -- but a *hung* one holds a thread for the life of
# the process, and the list in code is already a usable answer.
_DEFAULT_TIMEOUT = 5.0

# The credential file stores epoch milliseconds. Read as seconds, 1.79e12 lands in the
# year 58000 and no token is ever expired, which is the failure this constant is named
# to prevent.
_MS_PER_SECOND = 1000.0


@dataclass(frozen=True, slots=True)
class Credential:
    """
    An OAuth token from the Claude Code credential file, and when it stops working.

    ``expires_at`` is epoch *seconds* -- converted on the way in -- or ``None`` when the
    file carried no usable expiry. ``None`` means "cannot tell", and ``is_expired``
    treats it as not expired: refusing to probe on a missing field would turn a readable
    credential into a permanently stale list.
    """

    token: str
    expires_at: float | None


@dataclass(frozen=True, slots=True)
class Probed:
    """
    The API answered with at least one model, in the order it returned them.
    """

    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Unavailable:
    """
    No list could be established. ``detail`` says why, for the log.

    One arm rather than several because every cause has the same consequence here --
    the list in code stands in. ``cli_version`` splits "too old" from "could not tell"
    because those need different actions from the operator; this needs none.
    """

    detail: str


CatalogProbe = Probed | Unavailable


def parse_credential(text: str) -> Credential | None:
    """
    The OAuth token and expiry out of the credential file's contents, or ``None``. Pure.

    ``None`` for anything that is not the shape expected -- not JSON, not an object, no
    token, a token that is not a string. A malformed file is a failed probe, never a
    raise, because the caller is a startup path that has a usable answer without it.
    """
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None

    section = raw.get("claudeAiOauth")
    if not isinstance(section, dict):
        return None

    token = section.get("accessToken")
    if not isinstance(token, str) or not token:
        return None

    # bool is an int subclass and would divide cleanly into a nonsense expiry, so it is
    # excluded rather than allowed through the isinstance check below.
    stamp = section.get("expiresAt")
    expires_at: float | None = None
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        expires_at = float(stamp) / _MS_PER_SECOND

    return Credential(token, expires_at)


def is_expired(credential: Credential, now: float) -> bool:
    """
    Whether this token has passed its expiry. Pure; ``now`` is a parameter.

    Checked before the call rather than letting the API answer 401, which spends a
    round trip to learn something already on disk.
    """
    if credential.expires_at is None:
        return False
    return now >= credential.expires_at


def catalog(probe: CatalogProbe, fallback: tuple[str, ...] = FALLBACK_MODELS) -> tuple[str, ...]:
    """
    The list to offer, given what the probe produced. Pure.

    ``Probed`` with an empty tuple cannot occur -- ``probe_catalog`` reports an empty
    response as ``Unavailable`` -- and is handled anyway, because a caller constructing
    one by hand should still get a list a launcher can draw.
    """
    match probe:
        case Probed(models):
            return models or fallback
        case Unavailable():
            return fallback
        case _:
            assert_never(probe)


def read_credential(path: str | None = None) -> Credential | None:
    """
    Read and parse the Claude Code credential file, or ``None``. **Blocking.**

    Nothing raises: a missing file, a directory, a permission error and unreadable bytes
    all return ``None``, which ``probe_catalog`` turns into a reason.
    """
    target = Path(path).expanduser() if path is not None else Path(CREDENTIAL_PATH).expanduser()
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_credential(text)


def probe_catalog(
    *,
    path: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    now: float | None = None,
) -> CatalogProbe:
    """
    Ask the API which models exist. **Blocking.** Never raises.

    ``now`` defaults to the wall clock and is a parameter so the expiry branch can be
    exercised without one.
    """
    credential = read_credential(path)
    if credential is None:
        return Unavailable(f"no usable credential in {path or CREDENTIAL_PATH}")

    if is_expired(credential, time.time() if now is None else now):
        # Claude Code refreshes this file; a later run picks up the new token. Saying so
        # keeps the log from reading like a broken install.
        return Unavailable("the Claude Code credential has expired; it refreshes on next use")

    # Imported here rather than at module scope because `anthropic` pulls pydantic and
    # httpx behind it. This function only ever runs on a worker, so paying that import
    # there keeps it off the path to the first frame.
    try:
        import anthropic
    except ImportError as exc:
        return Unavailable(f"the anthropic SDK is not importable: {exc}")

    try:
        client = anthropic.Anthropic(
            auth_token=credential.token,
            # Not required by `/v1/models` -- verified against the live endpoint, which
            # answers without it -- but OAuth tokens do need it on `/v1/messages`, so it
            # is sent here to keep this call correct if it ever moves.
            default_headers={"anthropic-beta": "oauth-2025-04-20"},
            timeout=timeout,
            # The caller already has an answer worth drawing, so a retry only delays it.
            max_retries=0,
        )
        # Iterating the page auto-paginates; `.data` would silently be the first page.
        found = tuple(model.id for model in client.models.list())
    except Exception as exc:  # noqa: BLE001 - reported as a reason, not swallowed
        return Unavailable(f"{type(exc).__name__}: {exc}")

    if not found:
        return Unavailable("the API returned no models")
    return Probed(found)


@dataclass
class Catalog:
    """
    The offerable list, refreshed once per process on a worker.

    ``models`` is readable from the first frame and is the list in code until a probe
    replaces it, so nothing waits and no caller handles "not answered yet" -- the
    difference between a probed list and the fallback is not a distinction the launcher
    has to draw.

    **Once per process, not once per launch**, for the reason ``launcher.VersionGate``
    records: the answer cannot change under a running process in a way this application
    would notice, and re-probing per modal puts a network call behind a keystroke.

    Mutable and unsynchronised by design: ``poll`` is called from the draw and is the
    only writer of ``models``. The worker touches nothing but the queue.
    """

    models: tuple[str, ...] = FALLBACK_MODELS
    detail: str | None = None
    answered: bool = False
    probing: bool = False
    _inbox: queue.SimpleQueue[CatalogProbe] = field(default_factory=queue.SimpleQueue, repr=False)

    def request(self) -> threading.Thread | None:
        """
        Start the probe on a worker unless one has already run. Returns without waiting.

        The worker is returned so a caller that genuinely has to wait -- a test -- can
        join it.
        """
        if self.answered or self.probing:
            return None
        self.probing = True
        inbox = self._inbox

        def work() -> None:
            try:
                inbox.put(probe_catalog())
            except Exception as exc:  # noqa: BLE001 - `probe_catalog` documents no raise
                # Caught at the thread boundary anyway: an exception escaping here would
                # leave `probing` true forever, and the fallback list would be presented
                # as a probed one with nothing in the log to say otherwise.
                inbox.put(Unavailable(f"{type(exc).__name__}: {exc}"))

        worker = threading.Thread(target=work, name="pptmstr-model-catalog", daemon=True)
        worker.start()
        return worker

    def poll(self) -> None:
        """
        Take the worker's answer if it has arrived. Cheap enough to call every frame.
        """
        if self.answered:
            return
        try:
            probe = self._inbox.get_nowait()
        except queue.Empty:
            return
        self.models = catalog(probe)
        self.detail = probe.detail if isinstance(probe, Unavailable) else None
        self.answered = True
        self.probing = False
