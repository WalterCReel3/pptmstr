"""
The offerable model list, the handshake it is read from, and pptmstr's promise that it
handles no Claude credential.
"""

from __future__ import annotations

import ast
import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from pptmstr import model_catalog
from pptmstr.log import LOG
from pptmstr.model_catalog import Catalog, CatalogProbe, Probed, Unavailable

_PACKAGE = Path(model_catalog.__file__).resolve().parent

# The one module allowed to name the credential file: it names it to deny it to agents.
_DENIES_THE_CREDENTIAL = _PACKAGE / "sandbox.py"


def _sources() -> list[Path]:
    return sorted(_PACKAGE.rglob("*.py"))


def test_the_default_model_is_one_of_the_models_offered() -> None:
    """
    ``DEFAULT_MODEL`` is spelled separately from ``MODELS`` so the default cannot move
    when the list is reordered. Separate spellings drift, so this keeps the two honest --
    a default nothing offers is a combo whose initial value is not in it.
    """
    assert model_catalog.DEFAULT_MODEL in model_catalog.MODELS


def test_the_offered_models_are_distinct() -> None:
    assert len(set(model_catalog.MODELS)) == len(model_catalog.MODELS)


def test_the_scan_below_sees_the_package() -> None:
    """
    The two scans pass vacuously on an empty glob, so the glob is pinned first.
    """
    found = _sources()
    assert _DENIES_THE_CREDENTIAL in found
    assert _PACKAGE / "model_catalog.py" in found


def test_only_the_sandbox_deny_list_names_the_credential_file() -> None:
    """
    pptmstr reads no Claude credential; every model request goes through the bundled
    CLI. ``sandbox.py`` names ``~/.claude/.credentials.json`` to keep agents out of it,
    which is the only reason for the string to appear in the package.
    """
    naming = [
        path.relative_to(_PACKAGE).as_posix()
        for path in _sources()
        if ".credentials.json" in path.read_text(encoding="utf-8")
    ]
    assert naming == ["sandbox.py"]


def test_no_module_carries_the_oauth_token_or_its_beta_header() -> None:
    """
    ``claudeAiOauth`` is the credential file's token section and ``oauth-2025-04-20``
    the header an OAuth token needs on the API. Either in the package means pptmstr is
    handling the operator's sign-in itself.
    """
    for marker in ("claudeAiOauth", "oauth-2025-04-20"):
        naming = [
            path.relative_to(_PACKAGE).as_posix()
            for path in _sources()
            if marker in path.read_text(encoding="utf-8")
        ]
        assert naming == [], marker


def test_no_module_imports_the_anthropic_client() -> None:
    """
    The handshake probe asks the bundled CLI and nothing else. ``anthropic`` in an import
    is pptmstr building an API client of its own, which needs the credential it does
    not read. Every module, the probe's own included, so a move does not escape the scan.
    """
    importing = []
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            if any(name == "anthropic" or name.startswith("anthropic.") for name in names):
                importing.append(path.relative_to(_PACKAGE).as_posix())
    assert importing == []


def test_the_probe_module_imports_the_sdk_and_not_the_api_client() -> None:
    """
    The guard above passes on a module that imports nothing at all; this pins that the
    probe's import is in the tree for it to see, and that it is the agent SDK's.
    """
    tree = ast.parse((_PACKAGE / "model_catalog.py").read_text(encoding="utf-8"))
    modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "claude_agent_sdk" in modules
    assert not any(module.startswith("anthropic") for module in modules)


# -- the handshake parser ------------------------------------------------------------

# What ``get_server_info()`` answered on 2026-10-18, CLI 2.1.300, signed out: trimmed to
# ``models`` and two neighbours, with every key of each entry as it came.
MEASURED: dict[str, Any] = {
    "claude_code_version": "2.1.300",
    "account": {"tokenSource": "none", "apiProvider": "firstParty"},
    "models": [
        {
            "value": "default",
            "resolvedModel": "claude-opus-5-5",
            "displayName": "Default (recommended)",
            "description": "Use the default model (currently Opus 5.5) · $4/$20 per Mtok",
            "supportsEffort": True,
            "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
            "supportsAdaptiveThinking": True,
            "supportsFastMode": True,
            "supportsAutoMode": True,
            "supportsThinkingOff": False,
        },
        {
            "value": "opus",
            "resolvedModel": "claude-opus-5-5",
            "displayName": "Opus",
            "description": "Opus 5.5 · Best for everyday, complex tasks · $4/$20 per Mtok",
            "supportsEffort": True,
            "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
            "supportsAdaptiveThinking": True,
            "supportsFastMode": True,
            "supportsAutoMode": True,
            "supportsThinkingOff": False,
        },
        {
            "value": "fable",
            "resolvedModel": "claude-fable-5-1",
            "displayName": "Fable",
            "description": "Fable 5.1 · Most capable · $10/$50 per Mtok",
            "supportsEffort": True,
            "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
            "supportsAdaptiveThinking": True,
            "supportsAutoMode": True,
            "supportsThinkingOff": False,
        },
        {
            "value": "sonnet",
            "resolvedModel": "claude-sonnet-5-5",
            "displayName": "Sonnet",
            "description": "Sonnet 5.5 · Efficient for routine tasks · $2/$10 per Mtok",
            "supportsEffort": True,
            "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
            "supportsAdaptiveThinking": True,
            "supportsAutoMode": True,
            "supportsThinkingOff": False,
        },
        {
            "value": "haiku",
            "resolvedModel": "claude-haiku-5-5",
            "displayName": "Haiku",
            "description": "Haiku 5.5 · Fastest for quick answers · $0.10/$0.50 per Mtok",
            "supportsEffort": True,
            "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
            "supportsAdaptiveThinking": True,
            "supportsAutoMode": True,
            "supportsThinkingOff": False,
        },
    ],
}


def _entry(resolved: object, **rest: object) -> dict[str, object]:
    return {"value": "alias", "resolvedModel": resolved, **rest}


def test_the_measured_handshake_offers_each_resolved_id_once_in_its_order() -> None:
    """
    Ids, not aliases, and ``default`` and ``opus`` naming the same model is one entry
    where it first appears rather than two combo rows that launch the same thing.
    """
    assert model_catalog.from_handshake(MEASURED) == Probed(
        ("claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5", "claude-haiku-5-5")
    )


def test_an_alias_is_never_offered_in_place_of_a_missing_id() -> None:
    """
    ``value`` is an alias where one exists. Falling back to it would put ``sonnet`` in
    the combo, and a draft holding it would follow Anthropic's mapping from then on.
    """
    probe = model_catalog.from_handshake(
        {"models": [{"value": "sonnet"}, _entry("claude-haiku-5-5")]}
    )
    assert probe == Probed(("claude-haiku-5-5",), refused=1)


@pytest.mark.parametrize(
    "bad",
    [
        "claude-opus-5-5",
        None,
        ["resolvedModel", "claude-opus-5-5"],
        _entry(None),
        _entry(5),
        _entry(""),
        _entry("   "),
        _entry(["claude-opus-5-5"]),
    ],
    ids=[
        "a bare string",
        "null",
        "a list",
        "a null id",
        "a numeric id",
        "an empty id",
        "a blank id",
        "a list id",
    ],
)
def test_a_malformed_entry_is_refused_alone_and_the_rest_are_offered(bad: object) -> None:
    probe = model_catalog.from_handshake(
        {"models": [_entry("claude-opus-5-5"), bad, _entry("claude-haiku-5-5")]}
    )
    assert probe == Probed(("claude-opus-5-5", "claude-haiku-5-5"), refused=1)


def test_keys_the_parser_does_not_read_change_nothing() -> None:
    probe = model_catalog.from_handshake(
        {
            "a_key_from_a_later_cli": {"anything": 1},
            "models": [_entry("claude-opus-5-5", supportsTeleportation=True, tier=[1, 2])],
        }
    )
    assert probe == Probed(("claude-opus-5-5",))


def test_an_id_is_offered_without_the_whitespace_around_it() -> None:
    """
    The id is handed to ``--model``; padding would make it a model nothing serves.
    """
    assert model_catalog.from_handshake({"models": [_entry(" claude-opus-5-5\n")]}) == Probed(
        ("claude-opus-5-5",)
    )


@pytest.mark.parametrize(
    ("info", "says"),
    [
        (None, "no server info"),
        ([], "no server info"),
        ({"claude_code_version": "2.1.300"}, "no models list"),
        ({"models": None}, "NoneType, not a list"),
        ({"models": {"opus": "claude-opus-5-5"}}, "dict, not a list"),
        ({"models": "claude-opus-5-5"}, "str, not a list"),
        ({"models": []}, "no usable model (0 entries, 0 refused)"),
        ({"models": [{"value": "opus"}, 7]}, "no usable model (2 entries, 2 refused)"),
    ],
    ids=[
        "no answer",
        "not a mapping",
        "no models key",
        "null models",
        "models a mapping",
        "models a string",
        "an empty list",
        "nothing usable",
    ],
)
def test_a_handshake_with_nothing_to_offer_is_unavailable_and_says_why(
    info: object, says: str
) -> None:
    """
    ``None`` is what ``get_server_info`` returns while a connect is still in progress;
    the rest is the shape moving under an undocumented key. Each falls back, and the
    reason in the log names which.
    """
    probe = model_catalog.from_handshake(info)
    assert isinstance(probe, Unavailable)
    assert says in probe.detail
    assert model_catalog.catalog(probe) == model_catalog.MODELS


def test_a_hand_built_empty_answer_still_offers_the_list_in_code() -> None:
    assert model_catalog.catalog(Probed(())) == model_catalog.MODELS
    assert model_catalog.catalog(Probed(("claude-x",))) == ("claude-x",)


# -- the probe -----------------------------------------------------------------------


class _Client:
    """
    Stands in for ``ClaudeSDKClient`` and records what the probe asked of it.
    """

    made: list[_Client] = []
    answer: dict[str, Any] | None = MEASURED
    stall = 0.0

    def __init__(self, options: Any) -> None:
        self.options = options
        self.calls: list[str] = []
        _Client.made.append(self)

    async def connect(self, prompt: object = None) -> None:
        self.calls.append(f"connect {prompt!r}")
        try:
            await asyncio.sleep(_Client.stall)
        except asyncio.CancelledError:
            # The SDK's ``connect`` disconnects on any failure before re-raising.
            self.calls.append("disconnect")
            raise

    async def get_server_info(self) -> dict[str, Any] | None:
        self.calls.append("get_server_info")
        return _Client.answer

    async def disconnect(self) -> None:
        self.calls.append("disconnect")

    def __getattr__(self, name: str) -> Any:
        # `query`, `interrupt`, `set_model` ... anything the probe has no business with.
        raise AssertionError(f"the probe called {name}")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> type[_Client]:
    import claude_agent_sdk

    _Client.made, _Client.answer, _Client.stall = [], MEASURED, 0.0
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _Client)
    return _Client


def test_the_probe_connects_with_no_prompt_reads_the_handshake_and_disconnects(
    client: type[_Client],
) -> None:
    assert model_catalog.probe_handshake() == model_catalog.from_handshake(MEASURED)
    (made,) = client.made
    assert made.calls == ["connect None", "get_server_info", "disconnect"]


def test_the_probe_starts_no_server_the_operators_config_names(client: type[_Client]) -> None:
    """
    Without ``strict_mcp_config`` the CLI spawns every stdio server in the operator's
    ``.mcp.json``, settings and plugins on connect -- before anything could refuse one.
    """
    model_catalog.probe_handshake()
    (made,) = client.made
    assert made.options.strict_mcp_config is True
    assert made.options.mcp_servers == {}
    # Piped, so the CLI's diagnostics do not land in the terminal pptmstr was run from.
    assert made.options.stderr is not None


def test_a_probe_that_outlasts_its_timeout_is_unavailable_and_disconnected(
    client: type[_Client],
) -> None:
    client.stall = 5.0
    probe = model_catalog.probe_handshake(timeout=0.05)
    assert probe == Unavailable("the CLI did not finish its handshake within 0.05s")
    (made,) = client.made
    assert made.calls == ["connect None", "disconnect"]


def test_a_cli_that_fails_to_start_is_a_reason_not_a_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import claude_agent_sdk

    class Missing(_Client):
        async def connect(self, prompt: object = None) -> None:
            raise claude_agent_sdk.CLINotFoundError("Claude Code not found")

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", Missing)
    probe = model_catalog.probe_handshake()
    assert isinstance(probe, Unavailable)
    assert probe.detail.startswith("CLINotFoundError: ")


def test_a_handshake_that_answered_nothing_falls_back(client: type[_Client]) -> None:
    client.answer = None
    assert model_catalog.probe_handshake() == Unavailable(
        "the handshake answered with no server info"
    )


# -- the catalog the frame reads -----------------------------------------------------


def _answered(catalog: Catalog) -> None:
    worker = catalog.request()
    assert worker is not None
    worker.join(timeout=10)
    catalog.poll()


def test_an_unarmed_catalog_starts_nothing_and_offers_the_list_in_code() -> None:
    """
    ``AppState`` builds this one. A test, the fake driver or a script drawing a frame
    must not start a CLI to do it.
    """
    catalog = Catalog()
    assert catalog.request() is None
    catalog.poll()
    assert catalog.models == model_catalog.MODELS
    assert not catalog.probing and not catalog.answered


def test_the_list_is_the_list_in_code_until_the_probe_answers() -> None:
    gate = threading.Event()

    def slow() -> CatalogProbe:
        gate.wait(10)
        return Probed(("claude-x",))

    catalog = Catalog(probe=slow)
    worker = catalog.request()
    assert worker is not None
    catalog.poll()
    assert catalog.models == model_catalog.MODELS and catalog.probing
    gate.set()
    worker.join(timeout=10)
    catalog.poll()
    assert catalog.models == ("claude-x",)
    assert catalog.answered and not catalog.probing and catalog.detail is None


def test_the_probe_runs_once_per_process() -> None:
    calls: list[int] = []

    def probe() -> CatalogProbe:
        calls.append(1)
        return Probed(("claude-x",))

    catalog = Catalog(probe=probe)
    _answered(catalog)
    assert catalog.request() is None
    assert calls == [1]


def test_a_failed_probe_leaves_the_list_in_code_and_logs_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(LOG, "warn", lambda source, text: said.append((source, text)))
    catalog = Catalog(probe=lambda: Unavailable("the CLI did not finish its handshake"))
    _answered(catalog)
    assert catalog.models == model_catalog.MODELS
    assert catalog.detail == "the CLI did not finish its handshake"
    assert said == [("models", "offering the list in code: the CLI did not finish its handshake")]


def test_a_probe_that_raises_is_caught_at_the_thread_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(LOG, "warn", lambda source, text: None)

    def broken() -> CatalogProbe:
        raise RuntimeError("boom")

    catalog = Catalog(probe=broken)
    _answered(catalog)
    assert catalog.models == model_catalog.MODELS
    assert catalog.detail == "RuntimeError: boom"
    assert catalog.answered and not catalog.probing


def test_entries_the_handshake_refused_are_counted_in_the_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    said: list[str] = []
    monkeypatch.setattr(LOG, "info", lambda source, text: said.append(text))
    _answered(Catalog(probe=lambda: Probed(("claude-x",), refused=2)))
    assert said == ["the CLI offers 1 model(s); 2 entries named no model and were skipped"]
