"""
The containment configuration of the dangerously-autonomous mode.

Every assertion here is about a state in which the operator believes containment is on
and it is not, which is the only failure mode this configuration has: it produces no
output of its own, so a wrong value is invisible until a run that was supposed to be
confined was not. Each test is therefore named after the hazard rather than the key.

The keys are checked by absence as well as by value. ``autoAllowBashIfSandboxed``
defaults to *true* and ``allowUnsandboxedCommands`` defaults to *true*, so a test that
only reads a present key would pass on a configuration that dropped it.
"""

from __future__ import annotations

import json
import posixpath
from pathlib import Path
from typing import Any

import pytest

from pptmstr.brief import default_root
from pptmstr.sandbox import ALLOWED_DOMAIN, containment_settings


def _sandbox() -> dict[str, Any]:
    """
    The ``sandbox`` block as the CLI would parse it back out of the string.
    """
    parsed = json.loads(containment_settings())
    assert isinstance(parsed, dict)
    block = parsed["sandbox"]
    assert isinstance(block, dict)
    return block


def test_what_it_returns_is_a_json_string_the_cli_can_parse() -> None:
    """
    The SDK passes this value to ``--settings`` after a ``json.dumps`` that validates
    nothing, so a malformed string would reach the CLI and be rejected there -- at
    launch, in stderr pptmstr does not show.
    """
    raw = containment_settings()
    assert isinstance(raw, str)
    parsed = json.loads(raw)  # the assertion is that this does not raise
    assert set(parsed) == {"sandbox"}
    # The transport decides between "this is JSON" and "this is a file path" by testing
    # the stripped string's first and last character, so a value that parses but does
    # not look like an object would be read as a path to a file that does not exist.
    assert raw.startswith("{") and raw.endswith("}")


def test_the_egress_allowlist_is_default_deny_with_exactly_one_domain() -> None:
    """
    The proxy allows on the client-supplied hostname and does not terminate TLS, so
    every entry on this list is a channel a determined agent can front through. One
    entry is the design, not an oversight, and ``strictAllowlist`` is what makes the
    list a deny-by-default rather than an addition to whatever the CLI allows anyway.
    """
    network = _sandbox()["network"]
    assert network["strictAllowlist"] is True
    assert network["allowedDomains"] == [ALLOWED_DOMAIN]
    assert ALLOWED_DOMAIN == "api.anthropic.com"


def test_an_unavailable_sandbox_stops_the_launch_rather_than_warning() -> None:
    """
    Without ``failIfUnavailable`` a missing ``bubblewrap`` or ``socat`` makes the CLI
    warn and run unsandboxed. pptmstr never pipes the CLI's stderr into the window, so
    that warning reaches the terminal pptmstr was launched from and nowhere the
    operator is looking.
    """
    assert _sandbox()["failIfUnavailable"] is True


def test_the_model_cannot_retry_a_denied_command_outside_the_sandbox() -> None:
    """
    ``allowUnsandboxedCommands`` defaults to true, and true means a violation can be
    retried with ``dangerouslyDisableSandbox`` -- through the permission flow this mode
    has auto-approving ``Bash``. Omitting the key is the defect, so presence is asserted
    before the value.
    """
    sandbox = _sandbox()
    assert "allowUnsandboxedCommands" in sandbox
    assert sandbox["allowUnsandboxedCommands"] is False


def test_the_cli_side_auto_approve_is_switched_off_explicitly() -> None:
    """
    ``autoAllowBashIfSandboxed`` defaults to **true**. Leaving it unset would enable a
    second auto-approve path beside pptmstr's own gate, one the design never analysed,
    and the settings would still look correct to a reader scanning for wrong values.
    """
    sandbox = _sandbox()
    assert "autoAllowBashIfSandboxed" in sandbox
    assert sandbox["autoAllowBashIfSandboxed"] is False


def test_the_sandbox_is_actually_enabled() -> None:
    """
    ``enabled`` defaults to false, so the other three load-bearing keys are inert
    without it.
    """
    assert _sandbox()["enabled"] is True


def test_the_brief_directory_is_not_denied_along_with_the_credential_file() -> None:
    """
    The narrow deny on ``~/.claude/.credentials.json`` sits inside the directory
    ``brief.default_root()`` writes every brief to. Widening it to ``~/.claude`` would
    read as a tightening and would blind a session to its own premises, so the boundary
    is pinned: no denied path may be an ancestor of the brief root.
    """
    briefs = default_root()
    denied = [entry["path"] for entry in _sandbox()["credentials"]["files"]]
    assert "~/.claude/.credentials.json" in denied

    for path in denied:
        expanded = Path(posixpath.expanduser(path))
        assert expanded != briefs
        assert expanded not in briefs.parents


def test_every_credential_entry_is_a_denial() -> None:
    """
    ``mode`` is per-entry, so an entry with any other mode would sit in a list named
    for denials while granting one.
    """
    files = _sandbox()["credentials"]["files"]
    assert len(files) == 4
    assert all(entry["mode"] == "deny" for entry in files)
    assert {entry["path"] for entry in files} == {
        "~/.ssh",
        "~/.aws",
        "~/.claude/.credentials.json",
        "~/.claude.json",
    }


def test_no_command_is_excluded_from_the_sandbox() -> None:
    """
    Upstream suggests excluding ``git`` because some git operations fail under the
    sandbox. An ``excludedCommands`` entry for it would take ``git push`` back outside
    the egress allowlist, which is the single thing that allowlist exists to catch.
    """
    assert _sandbox().get("excludedCommands", []) == []


def test_no_key_outside_the_record_is_invented() -> None:
    """
    An unrecognised settings key is accepted silently by the CLI (§8c, arm C), so a
    misspelled or made-up key does not fail loudly -- it reads as configured and is
    ignored. The set is therefore pinned rather than merely spot-checked.
    """
    sandbox = _sandbox()
    assert set(sandbox) == {
        "enabled",
        "failIfUnavailable",
        "allowUnsandboxedCommands",
        "autoAllowBashIfSandboxed",
        "network",
        "credentials",
    }
    assert set(sandbox["network"]) == {"strictAllowlist", "allowedDomains"}
    assert set(sandbox["credentials"]) == {"files"}


def test_setting_the_typed_sandbox_field_too_would_discard_this_configuration() -> None:
    """
    ``containment_settings``' docstring asserts a fact about the installed SDK -- that
    ``_build_settings_value`` *assigns* ``settings_obj["sandbox"]`` instead of merging
    into it -- and the constraint it puts on the caller is only worth obeying while that
    fact holds. Pin it here so an SDK upgrade that changes the merge cannot leave the
    docstring asserting something untrue, which is the failure this repository keeps
    paying for.
    """
    sdk = pytest.importorskip("claude_agent_sdk")
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    raw = containment_settings()

    settings_only = SubprocessCLITransport("", sdk.ClaudeAgentOptions(settings=raw))
    assert settings_only._build_settings_value() == raw

    both = SubprocessCLITransport(
        "", sdk.ClaudeAgentOptions(settings=raw, sandbox={"enabled": True})
    )
    merged = both._build_settings_value()
    assert merged is not None
    assert json.loads(merged)["sandbox"] == {"enabled": True}


def test_it_reads_nothing_from_the_environment(monkeypatch: Any) -> None:
    """
    The tilde paths are expanded by the CLI, not here. If this function ever called
    ``Path.home()`` or read ``$HOME``, its output would depend on the process that
    built it rather than the process the sandbox runs in.
    """
    monkeypatch.setenv("HOME", "/nowhere/that/exists")
    under_a_different_home = containment_settings()
    monkeypatch.setenv("HOME", "/somewhere/else")
    assert containment_settings() == under_a_different_home
