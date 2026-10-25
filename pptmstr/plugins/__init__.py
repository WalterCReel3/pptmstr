"""
The plugin pptmstr ships inside its package and passes to every session it launches
(planning/2026-10-18-vector-displays-beside-typeset-text.md §10.5).

``pptmstr/plugins/pptmstr/`` is a Claude Code plugin directory: a manifest and
skills, nothing else. A plugin may also carry MCP servers, hooks, LSP servers or
monitors, and each of those starts a process from the CLI, outside the sandbox and
before the gate sees a tool call. tests/test_skill_ship.py holds the directory to
skills only.

It is passed as ``ClaudeAgentOptions.plugins`` with ``skills`` and
``setting_sources`` left unset, so the user's and the project's settings and
skills load exactly as they do without it: any value of ``skills`` makes the SDK
pass ``--setting-sources=user,project``, which drops ``settings.local.json``
(notes/vector-rendering/probe-skills.md §2).

No SDK import here: ``approval`` reads ``SKILLS``, and it stays free of the SDK.
"""

from __future__ import annotations

import atexit
import contextlib
import functools
from importlib import resources
from pathlib import Path
from typing import Literal, TypedDict

NAME = "pptmstr"
"""
The manifest's ``name``, which the CLI prefixes each of its skills with.
"""

FIGURES = "pptmstr-figures"

SKILLS = frozenset({f"{NAME}:{FIGURES}"})
"""
Every skill the shipped plugin holds, as a ``Skill`` call names it
(``tool_input["skill"]``). ``approval.classify`` admits exactly these, by
equality, so a name that merely starts with the prefix is not one of them.
"""


class PluginConfig(TypedDict):
    """
    ``claude_agent_sdk.SdkPluginConfig``'s shape, spelled here to keep the SDK out.
    """

    type: Literal["local"]
    path: str


# Holds an extraction open for the life of the process when the package is not a
# directory on disk (a zipped install), and is empty otherwise.
_HELD = contextlib.ExitStack()
atexit.register(_HELD.close)


@functools.cache
def root() -> Path:
    """
    The shipped plugin's directory, absolute. The CLI resolves a relative
    ``--plugin-dir`` against the session's cwd and not the launcher's, so a relative
    path would load nothing in any session started elsewhere (the probe's run 2).
    """
    found = resources.files(__name__) / NAME
    return Path(_HELD.enter_context(resources.as_file(found))).resolve()


def figures_skill() -> Path:
    """
    The SKILL.md ``figures.fences.skill_text`` is held equal to.
    """
    return root() / "skills" / FIGURES / "SKILL.md"


def config() -> PluginConfig:
    """
    What a launch passes in ``ClaudeAgentOptions.plugins``.
    """
    return {"type": "local", "path": str(root())}
