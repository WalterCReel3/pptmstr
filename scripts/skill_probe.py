#!/usr/bin/env python3
"""
Probe skill-probe: whether a skill shipped inside the pptmstr package loads into a
session through ``ClaudeAgentOptions.plugins``, alongside the user's and the
project's own skills.

    .venv/bin/python scripts/skill_probe.py [--live] [--out DIR]

Without ``--live`` every scenario stops as soon as the ``system``/``init`` frame
arrives, so no model turn is paid for: the frame is emitted before the first API
request. ``--live`` also runs the scenarios that need the model to act (does it
invoke the skill; does a sub-agent given ``AgentDefinition.skills`` see it).

Builds stub plugins and a stand-in project under ``--out`` (default
``$TMPDIR/skill-probe``) and writes ``results.json`` there. The write-up is
notes/vector-rendering/probe-skills.md.

**Nothing in ``pptmstr/``, ``~/.claude`` or this worktree's ``.claude/`` is
written.** A same-named "user" skill is simulated as a project skill in a stand-in
project directory under ``--out``, never by writing to ``~/.claude``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_agent_sdk import (  # noqa: E402
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

MODEL = "haiku"

SKILL_BODY = """\
---
name: {name}
description: {description}
---

PROBE-MARKER {marker}. When this skill is loaded, reply with the single line
"SKILL {marker} LOADED" and nothing else.
"""

FIGURES_DESCRIPTION = (
    "Use whenever you are about to draw a diagram, chart, graph, dependency graph "
    "or formula in a reply: how pptmstr renders mermaid, svg and math fences."
)


def write_plugin(
    base: Path, dirname: str, plugin_name: str | None, skill: str, marker: str
) -> Path:
    root = base / dirname
    if plugin_name is not None:
        (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": plugin_name,
                    "version": "0.0.0-probe",
                    "description": f"skill-probe stub ({marker})",
                    "author": {"name": "pptmstr"},
                },
                indent=2,
            )
        )
    skill_dir = root / "skills" / skill
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        SKILL_BODY.format(name=skill, description=FIGURES_DESCRIPTION, marker=marker)
    )
    return root


def write_project(base: Path, skill: str, marker: str) -> Path:
    project = base / "standin-project"
    skill_dir = project / ".claude" / "skills" / skill
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        SKILL_BODY.format(name=skill, description=FIGURES_DESCRIPTION, marker=marker)
    )
    return project


def children_of(pid: int) -> list[str]:
    """Every live descendant of ``pid`` as ``pid cmdline``, read from /proc."""
    by_parent: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        by_parent.setdefault(ppid, []).append(int(entry.name))
    out: list[str] = []
    stack = list(by_parent.get(pid, []))
    while stack:
        child = stack.pop()
        try:
            cmd = (Path("/proc") / str(child) / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        try:
            fields = (Path("/proc") / str(child) / "stat").read_text()
            comm = fields[fields.index("(") + 1 : fields.rindex(")")]
            state = fields.rsplit(")", 1)[1].split()[0]
        except (OSError, ValueError):
            comm, state = "?", "?"
        out.append(
            f"{child} comm={comm} state={state} cmd={cmd.decode(errors='replace').strip()[:160]}"
        )
        stack.extend(by_parent.get(child, []))
    return out


async def run(name: str, options: ClaudeAgentOptions, prompt: str, live: bool) -> dict[str, Any]:
    record: dict[str, Any] = {"scenario": name, "live": live}
    tool_calls: list[dict[str, Any]] = []
    texts: list[str] = []

    async def gate(input_data: Any, tool_use_id: Any, context: Any) -> dict[str, Any]:
        tool = input_data.get("tool_name")
        tool_calls.append(
            {
                "tool": tool,
                "input": input_data.get("tool_input"),
                "agent_id": input_data.get("agent_id"),
            }
        )
        # Skill and Agent run, everything else is refused: the probe is about
        # what is listed and invoked, not about what the skill then does.
        decision = "allow" if tool in ("Skill", "Agent", "Task") else "deny"
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": "skill-probe",
            }
        }

    options.hooks = {"PreToolUse": [HookMatcher(hooks=[gate])]}
    options.stderr = lambda line: record.setdefault("stderr", []).append(line)
    client = ClaudeSDKClient(options)
    try:
        await client.connect()
        transport = client._transport
        pid = transport._process.pid if transport is not None and transport._process else None  # type: ignore[attr-defined]
        record["cli_pid"] = pid
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, SystemMessage) and message.subtype == "init":
                data = message.data
                record["init"] = {
                    k: data.get(k)
                    for k in (
                        "claude_code_version",
                        "skills",
                        "plugins",
                        "plugin_errors",
                        "agents",
                        "apiKeySource",
                    )
                }
                record["init"]["tools_has_Skill"] = "Skill" in (data.get("tools") or [])
                record["init"]["slash_commands"] = data.get("slash_commands")
                if pid is not None:
                    record["children_at_init"] = children_of(pid)
                if not live:
                    break
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        record.setdefault("tool_use_blocks", []).append(
                            {
                                "name": block.name,
                                "input": block.input,
                                "parent": message.parent_tool_use_id,
                            }
                        )
            elif isinstance(message, ResultMessage):
                record["result"] = {
                    "is_error": message.is_error,
                    "subtype": message.subtype,
                    "cost_usd": message.total_cost_usd,
                    "num_turns": message.num_turns,
                    "result": (message.result or "")[:400],
                }
    except Exception as exc:  # noqa: BLE001 -- recorded, the next scenario still runs
        record["exception"] = f"{type(exc).__name__}: {exc}"[:600]
    finally:
        try:
            await client.disconnect()
        except Exception as exc:  # noqa: BLE001
            record["disconnect_exception"] = f"{type(exc).__name__}: {exc}"[:300]
    record["hook_tool_calls"] = tool_calls
    record["texts"] = [t[:600] for t in texts]
    return record


def base_options(cwd: Path, plugins: list[Path], **extra: Any) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=MODEL,
        cwd=str(cwd),
        plugins=[{"type": "local", "path": str(p)} for p in plugins],
        # pptmstr's own shape: dontAsk with a PreToolUse gate, and only the MCP
        # servers it names (here none).
        permission_mode="dontAsk",
        strict_mcp_config=True,
        max_turns=6,
        **extra,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--out", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "skill-probe")
    )
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    # Absolute: the CLI resolves a relative --plugin-dir against the session cwd,
    # which the collision scenarios move into the stand-in project.
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    shipped = write_plugin(out, "pptmstr-plugin", "pptmstr", "pptmstr-figures", "SHIPPED")
    same_plugin_name = write_plugin(
        out, "rival-same-plugin-name", "pptmstr", "pptmstr-figures", "RIVAL-SAMENAME"
    )
    other_plugin = write_plugin(
        out, "rival-other-plugin", "rival", "pptmstr-figures", "RIVAL-OTHER"
    )
    no_manifest = write_plugin(out, "pptmstr-nomanifest", None, "pptmstr-figures", "NOMANIFEST")
    project = write_project(out, "pptmstr-figures", "PROJECT")

    list_prompt = (
        "Without calling any tool, list verbatim every skill name in your available-skills "
        "listing, one per line, then stop."
    )
    draw_prompt = (
        "Draw me a dependency graph of three packages: "
        "app depends on core and ui, ui depends on core."
    )

    scenarios: list[tuple[str, ClaudeAgentOptions, str, bool]] = [
        ("A-baseline-no-plugin", base_options(ROOT, []), list_prompt, False),
        ("B-plugin-default", base_options(ROOT, [shipped]), list_prompt, False),
        (
            "C-plugin-skills-list",
            base_options(ROOT, [shipped], skills=["pptmstr:pptmstr-figures"]),
            list_prompt,
            False,
        ),
        (
            "C2-plugin-skills-bare-name",
            base_options(ROOT, [shipped], skills=["pptmstr-figures"]),
            list_prompt,
            False,
        ),
        ("D-plugin-skills-all", base_options(ROOT, [shipped], skills="all"), list_prompt, False),
        (
            "E-plugin-isolated-settings",
            base_options(ROOT, [shipped], setting_sources=[]),
            list_prompt,
            False,
        ),
        (
            "F-plugin-and-same-named-project-skill",
            base_options(project, [shipped]),
            list_prompt,
            False,
        ),
        (
            "G-two-plugins-same-plugin-name",
            base_options(ROOT, [shipped, same_plugin_name]),
            list_prompt,
            False,
        ),
        (
            "H-two-plugins-same-skill-name",
            base_options(ROOT, [shipped, other_plugin]),
            list_prompt,
            False,
        ),
        ("I-plugin-without-manifest", base_options(ROOT, [no_manifest]), list_prompt, False),
    ]
    if args.live:
        drawer = AgentDefinition(
            description="Draws diagrams on request.",
            prompt="You draw diagrams. Answer the request you are given.",
            tools=["Read"],  # no "Skill": whether skills= grants it is the question
            model="haiku",
            skills=["pptmstr:pptmstr-figures"],
        )
        drawer_noskills = AgentDefinition(
            description="Draws diagrams on request.",
            prompt="You draw diagrams. Answer the request you are given.",
            tools=["Read"],
            model="haiku",
        )
        drawer_inherit = AgentDefinition(
            description="Draws diagrams on request.",
            prompt="You draw diagrams. Answer the request you are given.",
            model="haiku",
        )
        sub_prompt = (
            "Use the Agent tool to start the 'drawer' agent with this exact request: "
            "'Without calling any tool other than Skill, list verbatim every skill name in your "
            "available-skills listing, one per line. Then draw a dependency graph: app depends on "
            "core and ui, ui depends on core.' Then repeat its answer verbatim."
        )
        scenarios += [
            ("L1-live-list-default", base_options(ROOT, [shipped]), list_prompt, True),
            ("L2-live-draw-default", base_options(ROOT, [shipped]), draw_prompt, True),
            (
                "L3-live-list-skills-filter",
                base_options(ROOT, [shipped], skills=["pptmstr:pptmstr-figures"]),
                list_prompt,
                True,
            ),
            (
                "L3b-live-list-skills-bare-name",
                base_options(ROOT, [shipped], skills=["pptmstr-figures"]),
                list_prompt,
                True,
            ),
            (
                "L3c-live-list-skills-all",
                base_options(ROOT, [shipped], skills="all"),
                list_prompt,
                True,
            ),
            ("L4-live-draw-collision", base_options(project, [shipped]), draw_prompt, True),
            (
                "L4b-live-draw-two-plugins-same-name",
                base_options(ROOT, [shipped, same_plugin_name]),
                draw_prompt,
                True,
            ),
            (
                "L5-live-subagent-skills",
                base_options(ROOT, [shipped], agents={"drawer": drawer}),
                sub_prompt,
                True,
            ),
            (
                "L6-live-subagent-no-skills-field",
                base_options(ROOT, [shipped], agents={"drawer": drawer_noskills}),
                sub_prompt,
                True,
            ),
            (
                "L7-live-subagent-inherits-tools",
                base_options(ROOT, [shipped], agents={"drawer": drawer_inherit}),
                sub_prompt,
                True,
            ),
        ]

    results = []
    for name, options, prompt, live in scenarios:
        if args.only and name not in args.only:
            continue
        print(f"== {name}", flush=True)
        record = await run(name, options, prompt, live)
        print(textwrap.indent(json.dumps(record, indent=2, default=str)[:6000], "  "), flush=True)
        results.append(record)
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"wrote {out / 'results.json'}")


if __name__ == "__main__":
    asyncio.run(main())
