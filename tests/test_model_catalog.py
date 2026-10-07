"""
The decisions behind the offerable model list.

Everything here is checkable without a socket: the network call lives behind one name
(``probe_catalog``) and the judgements it composes are pure, so the cases worth pinning
-- a stale token, a malformed file, a probe that answered with nothing -- are reachable
by calling those directly.
"""

from __future__ import annotations

import json

from pptmstr import model_catalog


def _credential(
    token: str = "sk-ant-oat01-test", expires_at: int | None = 1_790_367_940_954
) -> str:
    section: dict[str, object] = {"accessToken": token}
    if expires_at is not None:
        section["expiresAt"] = expires_at
    return json.dumps({"claudeAiOauth": section})


def test_the_default_model_is_one_of_the_models_offered() -> None:
    """
    ``DEFAULT_MODEL`` is spelled separately from ``FALLBACK_MODELS`` so the default
    cannot move when the list is reordered. Separate spellings drift, so this is the
    test that keeps the two honest -- a default nothing offers is a combo whose initial
    value is not in it.
    """
    assert model_catalog.DEFAULT_MODEL in model_catalog.FALLBACK_MODELS


def test_an_expiry_is_read_as_milliseconds_not_seconds() -> None:
    """
    The credential file stores epoch milliseconds. Read as seconds the same number lands
    tens of thousands of years out, every token reads as live, and the expiry check
    silently never fires -- so the bug is not a crash but a check that stopped existing.
    """
    credential = model_catalog.parse_credential(_credential(expires_at=1_790_367_940_954))
    assert credential is not None
    assert credential.expires_at is not None
    # Seconds, so within a decade of the timestamp it was built from rather than in
    # the year 58000.
    assert 1_700_000_000.0 < credential.expires_at < 1_900_000_000.0


def test_a_token_past_its_expiry_is_expired() -> None:
    credential = model_catalog.parse_credential(_credential(expires_at=1_000_000_000_000))
    assert credential is not None
    assert model_catalog.is_expired(credential, now=1_000_000_001.0)


def test_a_credential_with_no_expiry_is_not_treated_as_expired() -> None:
    """
    A missing field means "cannot tell", and refusing to probe on it would turn one
    unreadable key into a list that never refreshes again. The API still gets to say no.
    """
    credential = model_catalog.parse_credential(_credential(expires_at=None))
    assert credential is not None
    assert credential.expires_at is None
    assert not model_catalog.is_expired(credential, now=2_000_000_000.0)


def test_an_expired_credential_is_refused_without_a_network_call(tmp_path) -> None:
    """
    The point of reading ``expiresAt`` at all: a token already known to be dead costs
    a round trip and a 401 to discover. ``anthropic`` is never imported on this path,
    so the refusal is reached before any client is built.
    """
    path = tmp_path / "credentials.json"
    path.write_text(_credential(expires_at=1_000_000_000_000), encoding="utf-8")

    probe = model_catalog.probe_catalog(path=str(path), now=1_000_000_001.0)

    assert isinstance(probe, model_catalog.Unavailable)
    assert "expired" in probe.detail


def test_a_malformed_credential_file_is_a_reason_not_a_crash(tmp_path) -> None:
    """
    This runs during startup. A truncated or rewritten credential file must cost the
    probe, not the process.
    """
    path = tmp_path / "credentials.json"
    path.write_text("{not json at all", encoding="utf-8")

    probe = model_catalog.probe_catalog(path=str(path))

    assert isinstance(probe, model_catalog.Unavailable)
    assert "credential" in probe.detail


def test_a_missing_credential_file_is_a_reason_not_a_crash(tmp_path) -> None:
    probe = model_catalog.probe_catalog(path=str(tmp_path / "absent.json"))

    assert isinstance(probe, model_catalog.Unavailable)
    assert model_catalog.read_credential(str(tmp_path / "absent.json")) is None


def test_a_credential_without_the_oauth_section_is_unreadable() -> None:
    """
    Claude Code owns this file's shape. A future layout that moves the token must read
    as "no credential" rather than as a token of ``None``.
    """
    assert model_catalog.parse_credential(json.dumps({"somethingElse": {}})) is None
    assert model_catalog.parse_credential(json.dumps({"claudeAiOauth": {"accessToken": 7}})) is None
    assert model_catalog.parse_credential("[]") is None


def test_a_failed_probe_falls_back_to_the_list_in_code() -> None:
    """
    The probe is an override, not a dependency: a machine with no network still launches.
    """
    offline = model_catalog.Unavailable("offline")

    assert model_catalog.catalog(offline) == model_catalog.FALLBACK_MODELS


def test_a_probe_that_answered_with_nothing_falls_back() -> None:
    """
    An empty list is a degenerate success. Taken literally it draws a combo with no
    entries, which is strictly worse than the stale list it replaced.
    """
    assert model_catalog.catalog(model_catalog.Probed(())) == model_catalog.FALLBACK_MODELS


def test_a_probe_keeps_the_order_the_api_returned() -> None:
    """
    The response is newest-first and nothing here sorts it, so the model that just
    shipped is the one at the top of the list. Sorting would bury it.
    """
    returned = ("claude-opus-5-5", "claude-fable-5-1", "claude-opus-5")

    assert model_catalog.catalog(model_catalog.Probed(returned)) == returned


def test_the_catalog_offers_the_list_in_code_before_any_probe_answers() -> None:
    """
    Readable from the first frame, so no caller has to draw "not answered yet".
    """
    assert model_catalog.Catalog().models == model_catalog.FALLBACK_MODELS


def test_the_catalog_takes_a_probed_list_when_the_worker_answers() -> None:
    returned = ("claude-opus-5-5", "claude-sonnet-5")
    gate = model_catalog.Catalog()
    gate._inbox.put(model_catalog.Probed(returned))

    gate.poll()

    assert gate.models == returned
    assert gate.answered
    assert gate.detail is None


def test_the_catalog_keeps_the_reason_a_probe_failed() -> None:
    """
    The fallback list and a probed one are indistinguishable on screen by design, so
    the reason is the only place "this is stale" is recorded.
    """
    gate = model_catalog.Catalog()
    gate._inbox.put(model_catalog.Unavailable("offline"))

    gate.poll()

    assert gate.models == model_catalog.FALLBACK_MODELS
    assert gate.detail == "offline"


def test_the_catalog_probes_once_per_process() -> None:
    """
    A second request would put a network call behind whatever triggered it. ``request``
    returning None is how a caller can tell it did not start one.
    """
    gate = model_catalog.Catalog()
    gate.answered = True

    assert gate.request() is None


def test_polling_an_empty_inbox_leaves_the_list_alone() -> None:
    """
    ``poll`` runs every frame and almost always finds nothing.
    """
    gate = model_catalog.Catalog()
    gate.probing = True

    gate.poll()

    assert gate.models == model_catalog.FALLBACK_MODELS
    assert not gate.answered
