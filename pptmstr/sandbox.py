"""
The containment configuration, as a value.

``planning/2026-09-03-a-dangerously-autonomous-mode.md`` §8 fixes the settings that
make the dangerously-autonomous mode contained rather than merely under-gated. This
module builds them and does nothing else: no IO, no clock, no launch. A caller hands
the result to ``ClaudeAgentOptions.settings``.

**Why a hand-built JSON string rather than the SDK's typed field.** The installed
``SandboxSettings`` TypedDict declares exactly seven keys -- ``enabled``,
``autoAllowBashIfSandboxed``, ``excludedCommands``, ``allowUnsandboxedCommands``,
``network``, ``ignoreViolations`` and ``enableWeakerNestedSandbox`` -- and
``SandboxNetworkConfig`` declares no ``strictAllowlist``. Three of §8's keys
(``failIfUnavailable``, ``credentials``, ``network.strictAllowlist``) have no slot in
those types even though the CLI documents them and §8c measured two of them working.
The transport validates nothing it is handed on this path -- with ``settings`` set and
``sandbox`` left ``None``, ``_build_settings_value`` returns the string untouched -- so
the extra keys survive at runtime; the obstacle is mypy, which this repository runs.
Going through ``settings`` as text is what lets the configuration be both complete and
type-clean. That survival is a property of *this* path only, and
``containment_settings`` states the condition the caller has to keep.

**Why it is one function with no parameters.** Every value here is load-bearing in the
sense §8 gives -- each closes a state in which the operator believes containment is on
and it is not. A parameter would be an invitation to relax one at a call site, where
the reasoning for it is not visible.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["ALLOWED_DOMAIN", "DENIED_CREDENTIAL_FILES", "containment_settings"]

# One entry, and the reason is not thrift. §8b.4: the sandbox proxy allows on the
# client-supplied hostname and does not terminate TLS by default, so domain fronting is
# available through anything on this list. A second entry would be an exfiltration
# channel rather than a convenience.
ALLOWED_DOMAIN = "api.anthropic.com"

# §8 names these four and stops there. ``~`` is left unexpanded on purpose: the CLI
# expands it in its own process, and expanding it here would put ``Path.home()`` -- an
# environment read -- inside a function whose whole contract is that it is pure.
DENIED_CREDENTIAL_FILES = (
    "~/.ssh",
    "~/.aws",
    "~/.claude/.credentials.json",
    "~/.claude.json",
)


def containment_settings() -> str:
    """
    §8's containment configuration, as the JSON string ``settings`` accepts.

    **This string is the whole sandbox configuration, so the caller must leave
    ``ClaudeAgentOptions.sandbox`` at ``None``.** Setting both fields destroys every
    key below. The transport's ``_build_settings_value`` returns ``settings`` verbatim
    when ``sandbox`` is ``None``; when both are given it parses this string and then
    *assigns* -- ``settings_obj["sandbox"] = self._options.sandbox`` -- rather than
    merging, so the typed value replaces this block entire. ``json.dumps`` then
    succeeds, the CLI launches, and nothing warns. The typed ``SandboxSettings`` has
    no slot for ``failIfUnavailable``, ``credentials`` or ``network.strictAllowlist``,
    so the replacement cannot carry the four load-bearing keys even in principle: the
    obvious-looking split -- typed field for the keys that are typed, this string for
    the rest -- is exactly the arrangement that produces a silently unconfined run.

    Four of these keys are load-bearing:

    - ``network.strictAllowlist`` with ``allowedDomains``. Under auto-approved
      ``Bash`` the largest blast radius is not a stolen token but the tree being sent
      somewhere, and ``curl -F @file`` needs no credential. This is the control that
      catches that; the credential denials are an enumeration control aimed at a
      smaller hazard. If only one of these is ever kept, keep this one.

      The installed ``SandboxSettings`` docstring says in bold that filesystem and
      network restrictions are configured via permission rules "not via these sandbox
      settings", and sends the reader to ``WebFetch`` allow/deny rules for network.
      Read as advice about this key it is wrong, because it is about a different
      layer: ``WebFetch`` is a tool the CLI process performs, and that process runs
      *outside* the sandbox, so no ``WebFetch`` rule constrains a ``curl`` inside a
      sandboxed ``Bash`` command. ``strictAllowlist`` governs the sandbox's own
      network namespace, which is where that ``curl`` runs. §8c measured it: the
      out-of-allowlist HTTPS GET returned exit 56 and a ``<sandbox_violations>`` block
      reading ``deny network-outbound example.com:443 (host is not on the allow
      list)``, against ``200`` from the same command in the unsandboxed control arm.
    - ``failIfUnavailable``. On Linux the sandbox needs ``bubblewrap`` and ``socat``;
      without this key a missing dependency makes the CLI warn and run *unsandboxed*,
      and pptmstr never pipes the CLI's stderr into the window, so that warning is
      invisible from the UI.
    - ``allowUnsandboxedCommands: false``. Otherwise a violation lets the model retry
      with ``dangerouslyDisableSandbox``, which routes through the permission flow --
      the same flow this mode has auto-approving ``Bash``. Containment would be a
      one-parameter bypass the model can reach and the gate waves through.
    - ``autoAllowBashIfSandboxed: false``. It **defaults to true**, so the key is set
      rather than omitted: leaving it out enables a CLI-side auto-approve path the
      design never analysed.

    **``credentials.files`` deliberately does not deny ``~/.claude`` wholesale.**
    ``brief.default_root()`` puts every brief under ``~/.claude/projects/<slug>/``, so
    denying the directory blinds a session to its own premises. The four narrow files
    are the targets. What this buys is bounded and worth stating: the CLI process runs
    outside the sandbox and is already authenticated, so denying the credential file
    stops a ``Bash`` command reading the token -- it does not make the session
    un-credentialed.

    **There is no ``excludedCommands`` key.** Upstream suggests excluding ``git``
    because ``git merge`` and ``git checkout`` can fail under the sandbox; excluding it
    would remove containment for ``git`` including ``git push``, which is the exact
    egress ``strictAllowlist`` is here to deny. §8b.5 takes the failure instead.

    This configuration cannot protect itself against a CLI too old to recognise its
    keys -- an unrecognised settings key is accepted silently (§8c, arm C). The version
    floor that closes that is a separate control and does not live here.
    """
    settings: dict[str, Any] = {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": False,
            "network": {
                "strictAllowlist": True,
                "allowedDomains": [ALLOWED_DOMAIN],
            },
            "credentials": {
                "files": [{"path": path, "mode": "deny"} for path in DENIED_CREDENTIAL_FILES]
            },
        }
    }
    return json.dumps(settings)
