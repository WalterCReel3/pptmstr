"""
The containment configuration, as a value.

``planning/2026-09-03-a-dangerously-autonomous-mode.md`` §8 fixes the settings that
make the dangerously-autonomous mode contained rather than merely under-gated. This
module builds them and does nothing else: no IO, no clock, no launch, and one variable
of the environment read (``denied_records``). A caller hands the result to
``ClaudeAgentOptions.settings``.

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
import os
import posixpath
from typing import Any

__all__ = [
    "ALLOWED_DOMAIN",
    "DENIED_CREDENTIAL_FILES",
    "containment_settings",
    "denied_record_writes",
    "denied_records",
]

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


def denied_records() -> str:
    """
    The directory every session's records are in, as a sandbox path: what
    ``sessionfile.root()`` names, with the home directory left as ``~``.

    The rule is ``settings.config_dir``'s, spelled again so that this module imports
    nothing of pptmstr's and expands no ``~``: an absolute ``XDG_CONFIG_HOME`` is where
    the directory is, and anything else is ``~/.config``. That variable is read here,
    and it is this process's that is the right one to read: the records are wherever
    *this* process writes them. ``tests/test_sandbox.py`` holds the two spellings to
    one answer.
    """
    raw = os.environ.get("XDG_CONFIG_HOME", "")
    base = raw if raw.startswith("/") else "~/.config"
    return posixpath.join(base, "pptmstr", "sessions")


def denied_record_writes() -> tuple[str, str]:
    """
    Both directories pptmstr keeps sessions' records under, as sandbox paths: the
    sessions root and the legacy ``boards`` directory ``boardsync.migrate`` moves
    logs out of at startup.

    For ``filesystem.denyWrite``, because ``credentials.files`` is a read
    restriction -- upstream documents a ``deny`` entry there as "the same
    restriction that ``filesystem.denyRead`` applies" -- and the sandbox's write
    region is the session's directory. A session started in the home directory has
    both roots inside that region, and pptmstr reads what is under them back as
    what happened in a session.
    """
    sessions = denied_records()
    return sessions, posixpath.join(posixpath.dirname(sessions), "boards")


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

    **The directory of every session's records is denied whole** (``denied_records``),
    the session's own among them. What is under it is other sessions' command output,
    the operator's reasons for refusing them and their boards, none of which a
    contained session is given by any tool; and a command's output can hold what lets
    a process be taken for that command's (``commandrun``, on the tag). It is one more
    entry of ``credentials.files`` and not a ``filesystem.denyRead``: upstream documents
    the two as the same restriction, and this is the key whose four entries are seen
    to be applied (a contained session's own sandbox lists exactly them as denied).

    A session's own directory is not re-opened inside it. ``filesystem.allowRead`` can
    express that -- upstream documents the narrower path as winning -- but its
    directory is named by the session's id, a new session's id is made by the driver
    after this string is built, and this function takes no parameter for the reason
    the module gives. The cost is that a sandboxed command cannot read the log of the
    session's own command at the path ``read_commands`` names.

    **This bounds a ``Bash`` command and nothing else.** The CLI's ``Read``, ``Grep``
    and ``Glob`` run in the CLI's own process and follow permission rules; upstream
    says in so many words that a ``denyRead`` entry does not stop the ``Read`` tool.
    What stands in front of those is the gate: ``approval.records_read`` takes a
    read of another session's records off the allowlist on every rung, and leaves
    the session's own readable, which this entry does not.

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
            "filesystem": {"denyWrite": list(denied_record_writes())},
            "credentials": {
                "files": [
                    {"path": path, "mode": "deny"}
                    for path in (*DENIED_CREDENTIAL_FILES, denied_records())
                ]
            },
        }
    }
    return json.dumps(settings)
