"""
The offerable model list, and pptmstr's promise that it handles no Claude credential.
"""

from __future__ import annotations

from pathlib import Path

from pptmstr import model_catalog

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
