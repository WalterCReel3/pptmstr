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

from pptmstr.brief import default_root, legacy_root
from pptmstr.sandbox import ALLOWED_DOMAIN, containment_settings, denied_records


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


@pytest.mark.real_config_dir
def test_no_brief_directory_is_denied_to_a_sandboxed_read() -> None:
    """
    A worker lists and reads its premises with a command as readily as with
    ``Read``, so no read denial may cover the root briefs are written to, nor the
    legacy one a resumed session is still handed. The narrow deny on
    ``~/.claude/.credentials.json`` sits inside the second; widening it to
    ``~/.claude`` would read as a tightening and would blind those sessions. The
    records denial sits beside the first; widening it to the configuration
    directory would blind every team.
    """
    denied = [entry["path"] for entry in _sandbox()["credentials"]["files"]]
    assert "~/.claude/.credentials.json" in denied

    for briefs in (default_root(), legacy_root()):
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
    assert len(files) == 5
    assert all(entry["mode"] == "deny" for entry in files)
    assert {entry["path"] for entry in files} == {
        "~/.ssh",
        "~/.aws",
        "~/.claude/.credentials.json",
        "~/.claude.json",
        "~/.config/pptmstr/sessions",
    }


@pytest.mark.real_config_dir
def test_no_session_reads_another_sessions_records_from_a_sandboxed_command(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """
    The denied directory is the one the records are written under, wherever that
    is: ``denied_records`` spells ``settings.config_dir``'s rule a second time, and
    a deny at the default place while ``XDG_CONFIG_HOME`` put the records elsewhere
    would be a containment that reads as on. Every session's directory, and every
    command's log in it, is under what is denied; nothing re-opens any part of it.
    """
    from pptmstr import commandfile, sessionfile, settings

    for xdg in (None, str(tmp_path / "xdg"), "relative/is/unset"):
        if xdg is None:
            monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        else:
            monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
        sandbox = _sandbox()
        denied = [posixpath.expanduser(e["path"]) for e in sandbox["credentials"]["files"]]
        # What ``sessionfile.root()`` is where no test has moved it.
        root = settings.config_dir() / sessionfile._SESSIONS
        assert posixpath.expanduser(denied_records()) == str(root)
        assert str(root) in denied
        log = commandfile.command_dir("some-session", "cmd-20260101T000000Z-0123456789ab", root)
        assert log is not None and root in log.parents
        assert set(sandbox["filesystem"]) == {"denyWrite"}
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert denied_records() == str(tmp_path / "xdg" / "pptmstr" / "sessions")


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
        "filesystem",
        "credentials",
    }
    assert set(sandbox["network"]) == {"strictAllowlist", "allowedDomains"}
    assert set(sandbox["filesystem"]) == {"denyWrite"}
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


@pytest.mark.real_config_dir
def test_no_sandboxed_command_writes_either_records_root(monkeypatch: Any, tmp_path: Path) -> None:
    """
    The write region is the session's directory, and one started in the home
    directory has the records inside it. ``credentials.files`` denies reads only.
    """
    from pptmstr import sessionfile, settings

    for xdg in (None, str(tmp_path / "xdg")):
        if xdg is None:
            monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        else:
            monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
        denied = [posixpath.expanduser(p) for p in _sandbox()["filesystem"]["denyWrite"]]
        assert denied == [
            str(settings.config_dir() / sessionfile._SESSIONS),
            str(settings.config_dir() / "boards"),
            str(settings.config_dir() / "briefs"),
        ]
