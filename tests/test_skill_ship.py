"""
The plugin pptmstr ships inside its package (``pptmstr.plugins``): it holds skills
and nothing that starts a process, its one skill is the text ``figures.fences``
builds from the table, the gate's list of shipped names is what the directory
holds, and every launch passes it by an absolute path without changing which of
the user's and the project's settings load.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pptmstr import plugins
from pptmstr.bridge import Bridge
from pptmstr.driver import AgentSession
from pptmstr.figures import fences
from pptmstr.sandbox import containment_settings
from pptmstr.templates import FEATURE

RESUMED = "11111111-2222-3333-4444-555555555555"

# Manifest keys and top-level entries that make the CLI start something: an MCP
# server, a hook command, a language server or a monitor, each outside the sandbox
# (plugins reference, "Standard layout"; sandbox.py on stdio MCP servers). Commands
# and agents start nothing but are not what this plugin is for, so they are refused
# too.
SPAWNING_KEYS = {
    "mcpServers",
    "hooks",
    "lspServers",
    "monitors",
    "commands",
    "agents",
    "outputStyles",
}
SPAWNING_ENTRIES = {
    ".mcp.json",
    ".lsp.json",
    "hooks",
    "monitors",
    "commands",
    "agents",
    "bin",
    "output-styles",
}


def _files(root: Path) -> set[str]:
    return {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}


# -- the directory -----------------------------------------------------------------


def test_the_plugin_resolves_to_an_absolute_directory_in_the_package() -> None:
    root = plugins.root()
    assert root.is_absolute() and root.is_dir()
    assert root == (Path(plugins.__file__).parent / plugins.NAME).resolve()
    assert plugins.config() == {"type": "local", "path": str(root)}


def test_the_plugin_ships_skills_and_nothing_that_starts_a_process() -> None:
    root = plugins.root()
    assert _files(root) == {
        ".claude-plugin/plugin.json",
        f"skills/{plugins.FIGURES}/SKILL.md",
    }
    assert not {entry.name for entry in root.iterdir()} & SPAWNING_ENTRIES
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == plugins.NAME
    assert not set(manifest) & SPAWNING_KEYS, set(manifest) & SPAWNING_KEYS


def test_the_gate_admits_exactly_the_skills_the_directory_holds() -> None:
    skills = plugins.root() / "skills"
    held = {f"{plugins.NAME}:{path.name}" for path in skills.iterdir() if path.is_dir()}
    assert plugins.SKILLS == held
    assert plugins.SKILLS == {fences.SKILL}, "the floor names the skill the gate admits"
    for path in skills.iterdir():
        head = (path / "SKILL.md").read_text().split("\n---\n", 1)[0]
        assert f"\nname: {path.name}\n" in head, "the frontmatter name is the directory's"


def test_the_shipped_skill_is_the_text_the_table_builds() -> None:
    """
    Held by this test rather than written at load: an installed package directory
    may not be writable, and the path a session loads has to be the shipped one.
    To refresh it after the table changes:
    ``.venv/bin/python -c "from pptmstr import plugins; from pptmstr.figures import
    fences; plugins.figures_skill().write_text(fences.skill_text())"``.
    """
    assert plugins.figures_skill().read_text() == fences.skill_text()


# -- every launch passes it --------------------------------------------------------

LAUNCHES: dict[str, dict[str, Any]] = {
    "fresh solo": {},
    "resumed solo": {"resume": RESUMED},
    "fresh team": {"template": FEATURE},
    "resumed team": {"template": FEATURE, "resume": RESUMED},
    "contained": {"containment": containment_settings()},
    "elsewhere": {"cwd": "/srv/tree"},
}


@pytest.mark.parametrize("launch", list(LAUNCHES))
def test_every_launch_passes_the_plugin_and_leaves_the_settings_alone(launch: str) -> None:
    """
    ``skills`` and ``setting_sources`` stay unset on every launch: any value of
    ``skills`` makes the SDK pass ``--setting-sources=user,project``, which drops
    the project's ``settings.local.json``.
    """
    options = AgentSession(Bridge(), task="t", **LAUNCHES[launch])._options()
    assert options.plugins == [plugins.config()]
    assert Path(options.plugins[0]["path"]).is_absolute()
    assert options.skills is None
    assert options.setting_sources is None
    if options.agents:
        assert all(agent.skills is None for agent in options.agents.values())


def test_the_argv_carries_the_plugin_and_no_setting_sources() -> None:
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    options = AgentSession(Bridge(), task="t", template=FEATURE)._options()
    transport = SubprocessCLITransport("", options)
    transport._cli_path = "claude"
    argv = transport._build_command()
    assert argv[argv.index("--plugin-dir") + 1] == str(plugins.root())
    assert not any(arg.startswith("--setting-sources") for arg in argv)
    assert "--allowedTools" not in argv
