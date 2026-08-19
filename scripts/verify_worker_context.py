#!/usr/bin/env python3
"""
What is actually in a worker's context when it starts?

Four probes exist for sub-agents -- `verify_subagents.py`, `verify_subagent_usage.py`,
`verify_message_bus.py`, `verify_wake_path.py` -- and every one of them measures the
*outbound* direction: do the hooks fire, is `agent_id` attached, is `usage` populated,
what is a valid `to` address. **Nothing has ever measured what a sub-agent was given.**

That gap decides the shape of a fix. `driver.AgentSession._team` sets only
`description`, `prompt`, `tools` and `model` on each `AgentDefinition`; the installed
SDK also offers `memory`, `skills` and `initialPrompt`, all left at their defaults.
`ClaudeAgentOptions.setting_sources` is never set, which the SDK documents as "all
sources are loaded ... must include `project` to load CLAUDE.md files" -- so the
*session* gets project memory. Whether that reaches a programmatically-defined
sub-agent with `memory=None` is not stated anywhere, and the existence of a per-agent
`memory` field is the only hint either way. If workers already see CLAUDE.md, carrying
settled premises to them is a pointer and a read primitive; if they do not, it is
plumbing first.

Asking a worker "can you see CLAUDE.md?" measures nothing -- that is narration, and
STYLE.md §2 rules it out. So each channel gets a **canary**: a nonce reachable only
through that channel, with every tool that could reach it another way denied at the
gate. A worker that reports the nonce saw the channel; one that reports NONE did not.
The answers come back through a probe-owned MCP tool rather than as prose, so what is
captured is a tool call, not a sentence about one.

  1. Does a worker get project CLAUDE.md? `memdefault` (memory unset) and
     `memproject` (memory="project") answer it separately, so the field's effect is
     measured rather than assumed. The root answers as a control: if the *session*
     misses the canary too, the isolation is wrong and nothing else here is readable.
  2. Does the lead's briefing leak into a worker? `_system_prompt` appends
     `lead_briefing` for the lead alone. This is the negative control -- an expected
     NONE. If a worker reports the briefing canary, the canary method cannot
     discriminate and questions 1 and 3 are void.
  3. Does the CLI's sibling roster reach a worker, and is it conditional on
     `SendMessage`? Design §2.7 says a roster arrives for agents whose tool list
     carries `SendMessage`. `READ_ONLY_TOOLS` excludes it and `tools=None` inherits
     it, so `reviewer`/`investigator`/`skeptic` and `builder` would differ -- the
     roles whose job is to disagree would be the ones told least about who exists.
     `rosterbroad` (inherit) and `rosternarrow` (explicit list, no SendMessage) are
     that pair. Role names carry the run nonce, so naming a *sibling* is proof.
  4. Does the `Skill` tool reach a worker? Weaker than the others and labelled as
     such: a PreToolUse for `Skill` proves the worker had it, but no hook proves
     nothing -- a model that was never given the tool and a model that declined to
     call it look identical from here.
  5. **Can a worker read an absolute path outside its cwd?**
     `planning/2026-08-17-a-session-premise-is-a-place-not-a-message.md` puts a
     session's brief under `~/.claude/projects/<cwd-slug>/briefs/<session-id>/`,
     beside the transcripts the CLI already writes there, and records that the whole
     design rests on this being readable and that it is *not measured*. Two files,
     because they can plausibly differ: one in a temp directory that is merely
     outside cwd, one at the real `~/.claude/projects/...` shape. The answer is taken
     from `tool_response` on PostToolUse -- the bytes the tool returned -- rather than
     from the worker's account of them.
  6. **Does `AgentDefinition.initialPrompt` reach a worker?** It is the one
     *per-session* channel into a worker the SDK offers; `driver._team` leaves it
     unset and nothing has ever asked whether it arrives. It matters because every
     other channel is either per-build (`worker_prompt`) or the lead retyping, and a
     *pointer* placed there is a pointer rather than the second copy
     `2026-08-15-an-operator-instruction-the-lead-cannot-see.md` refuses.

**Everything except the report tool, the spawn and two named absolute paths is denied
at the gate**, so a canary cannot arrive by Read or Bash. The two readable paths are
matched exactly, and the workspace `CLAUDE.md` is not one of them, so allowing question
5's read does not put question 1's canary within reach. The spawn arguments are
recorded and scanned for the nonces afterwards: the lead is told to pass "begin" and
nothing else, and if it pastes a canary in anyway that arm is void rather than quietly
wrong.

**Questions 1-4 and their four roles are unchanged from run `04e4db`, deliberately.**
The prompts those arms answer are byte-identical, so a re-run is comparable to the one
the planning records cite. Questions 5 and 6 arrive as two new roles with their own
prompts rather than as two more lines in the shared one.

Usage:  .venv/bin/python scripts/verify_worker_context.py [--model claude-sonnet-5]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_agent_sdk import (  # noqa: E402
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    create_sdk_mcp_server,
    tool,
)

REPORT_TOOL = "mcp__probe__report"
SPAWN_TOOLS = ("Task", "Agent")
STAMP = "_agent"

# One nonce per channel. Generated per run so a canary cannot be recalled from a
# previous run's transcript, and short enough to survive a model repeating it.
RUN = uuid.uuid4().hex[:6]
MEMORY_CANARY = f"MEM-{uuid.uuid4().hex[:12]}"
BRIEFING_CANARY = f"BRF-{uuid.uuid4().hex[:12]}"
OUTSIDE_CANARY = f"OUT-{uuid.uuid4().hex[:12]}"
BRIEFDIR_CANARY = f"BRD-{uuid.uuid4().hex[:12]}"
INITIAL_CANARY = f"INI-{uuid.uuid4().hex[:12]}"

# Role names carry the run nonce because question 3 turns on a worker naming a
# *sibling*: a name it could not have guessed and was never told in its own prompt.
MEM_DEFAULT = f"memdefault-{RUN}"
MEM_PROJECT = f"memproject-{RUN}"
ROSTER_BROAD = f"rosterbroad-{RUN}"
ROSTER_NARROW = f"rosternarrow-{RUN}"
PATH_READ = f"pathread-{RUN}"
INIT_SEED = f"initseed-{RUN}"
ROLES = (MEM_DEFAULT, MEM_PROJECT, ROSTER_BROAD, ROSTER_NARROW, PATH_READ, INIT_SEED)

# What every worker is asked, in its own AgentDefinition.prompt rather than in the
# spawn prompt -- the lead must not be the one carrying the questions, or a leak from
# its context would be indistinguishable from a channel that works.
WORKER_TASK = """\
You are a probe worker. Answer three questions by calling the tool {report}, once per
question, and then stop. Do not use any other tool; every other tool is denied and
retrying one wastes the run.

Answer ONLY from the instructions and context you were given at startup. You have no
file access. If something is not present in your own context, the answer is the exact
word NONE -- do not guess, and do not infer it from the shape of these questions.

Call {report} three times, each with who="{name}":

1. question="memory", answer=the value of MEMORY_CANARY if it appears anywhere in your
   instructions or context, otherwise NONE.
2. question="briefing", answer=the value of BRIEFING_CANARY if it appears anywhere in
   your instructions or context, otherwise NONE.
3. question="roster", answer=a comma-separated list of the names of OTHER agents you
   were told about at startup -- names that are not your own and did not appear in
   this prompt. If you were told of no other agents, answer NONE.

Then reply with the single word done.
"""

# Appended to ROSTER_BROAD only. Its negative is inconclusive by construction, which
# is why it is not asked of every role: one attempt is enough to prove possession.
SKILL_PROBE = """
After the three reports, make exactly one attempt to call the Skill tool with
skill="probe-nonexistent". It will be denied; that is expected and the denial is the
measurement. If you have no Skill tool, skip this step and do not mention it.
"""

# Question 5. Its own role rather than two more lines in WORKER_TASK, so the four arms
# that answered run 04e4db keep the prompt they answered it with.
#
# The briefing question rides along because it is the negative control: a worker that
# reports BRIEFING_CANARY makes its own other answers unreadable, and that has to be
# checkable per role rather than assumed from the roles that were asked.
PATH_READ_TASK = """\
You are a probe worker. Answer by calling the tool {report}, once per question, and
then stop.

Call {report} three times, each with who="{name}":

1. question="briefing", answer=the value of BRIEFING_CANARY if it appears anywhere in
   your instructions or context, otherwise the exact word NONE. Do not guess.
2. question="outside", answer=the value of OUTSIDE_CANARY. It is not in your context.
   Read this exact absolute path to find it:
     {outside}
   If the read fails for any reason, answer with the word FAILED followed by the error
   text you were given, verbatim.
3. question="briefdir", answer=the value of BRIEFDIR_CANARY. It is not in your context.
   Read this exact absolute path to find it:
     {briefdir}
   If the read fails for any reason, answer with the word FAILED followed by the error
   text you were given, verbatim.

Read is the only other tool you have and those two paths are the only ones it will
open. Do not try any other path or tool.

Then reply with the single word done.
"""

# Question 6. `initialPrompt` is set on this role and on no other; the canary exists
# nowhere else, so reporting it is proof the field reached the worker.
INIT_SEED_TASK = """\
You are a probe worker. Answer two questions by calling the tool {report}, once per
question, and then stop. Do not use any other tool; every other tool is denied.

Answer ONLY from the instructions and context you were given at startup. You have no
file access. If something is not present in your own context, the answer is the exact
word NONE -- do not guess, and do not infer it from the shape of these questions.

Call {report} twice, each with who="{name}":

1. question="briefing", answer=the value of BRIEFING_CANARY if it appears anywhere in
   your instructions or context, otherwise NONE.
2. question="initial", answer=the value of INITIAL_CANARY if it appears anywhere in
   your instructions or context, otherwise NONE.

Then reply with the single word done.
"""

# The only place INITIAL_CANARY exists. Not on disk, not in a prompt we build into any
# other role, and not in anything the lead can see.
INIT_SEED_PROMPT = f"""\
Before you begin: INITIAL_CANARY={INITIAL_CANARY}
"""

LEAD_PROMPT = f"""\
Start six sub-agents, all in the same message so they run at once. Use the Agent tool
(or Task, whichever you have) with these subagent_type values:

  {MEM_DEFAULT}
  {MEM_PROJECT}
  {ROSTER_BROAD}
  {ROSTER_NARROW}
  {PATH_READ}
  {INIT_SEED}

For every one of them the prompt argument must be exactly the word: begin

Do not write anything else into a spawn prompt. Do not summarise your instructions,
do not pass along any identifier, code or value from your own context, and do not
explain the task -- each agent already carries its own instructions. Passing anything
else invalidates the run.

Before you spawn them, call {REPORT_TOOL} once with who="root", question="memory", and
answer set to the value of MEMORY_CANARY if it appears in your own context, or the
exact word NONE if it does not.

When all six have finished, reply with the single word finished.
"""

observed: dict[str, Any] = {
    "reports": [],  # what the report handler received, with the gate's stamp
    "spawns": [],  # Agent/Task tool_input, scanned for canary leakage
    "denied": [],  # every tool call refused by the gate, with its caller
    "system_messages": [],  # subtype + keys, to see whether per-agent init exists
    # The CLI spells this `memory_paths`, not `memoryFiles`. The SDK's own TypedDict
    # says the latter, and reading only that key reported null on a run where the
    # paths were in fact on the wire -- so both are collected and the key that
    # answered is kept beside the value.
    "memory_key": None,
    "memory_paths": None,
    "inits": [],  # one entry per init: does a sub-agent get its own?
    # Question 5's actual evidence. A worker's account of a failed read is narration;
    # `tool_response` on PostToolUse is what the tool returned. Both the attempt and
    # its result are kept, because "never attempted" and "attempted and refused" are
    # different answers and only one of them is the one being asked for.
    "reads": [],
    "result_error": None,
}

# The absolute paths question 5 may open, filled in by `main` once the run's
# directories exist. Matched exactly: nothing else is readable, so the workspace
# CLAUDE.md stays out of reach and question 1's canary is still unreachable by Read.
READABLE: dict[str, str] = {}


def _worker_prompt(name: str, extra: str = "") -> str:
    return WORKER_TASK.format(report=REPORT_TOOL, name=name) + extra


def _leaked(text: str) -> list[str]:
    """Which canaries appear in a string the lead wrote."""
    return [
        c
        for c in (MEMORY_CANARY, BRIEFING_CANARY, OUTSIDE_CANARY, BRIEFDIR_CANARY, INITIAL_CANARY)
        if c in text
    ]


async def main(model: str) -> int:
    workspace = Path(tempfile.mkdtemp(prefix="pptmstr-ctx-"))
    # The only place MEMORY_CANARY exists on disk. cwd is a fresh temp directory so
    # the repository's own CLAUDE.md is not in the lookup path and cannot answer for
    # it -- a canary found here came from project memory or from nowhere.
    (workspace / "CLAUDE.md").write_text(
        "# Probe workspace\n\n"
        f"MEMORY_CANARY={MEMORY_CANARY}\n\n"
        "This file exists only to be found (or not) by a sub-agent.\n",
        encoding="utf-8",
    )

    # Question 5's two targets. They differ in one property and it is the one that
    # could plausibly matter: `outside` is merely not under cwd, while `briefdir` is
    # the exact shape the premise record proposes -- the CLI's own project directory,
    # beside the transcripts it already writes there. A CLI that special-cases its own
    # state directory would answer the two differently, and one path could not tell.
    outside_dir = Path(tempfile.mkdtemp(prefix="pptmstr-outside-"))
    outside_file = outside_dir / "000-premises.md"
    outside_file.write_text(f"OUTSIDE_CANARY={OUTSIDE_CANARY}\n", encoding="utf-8")

    slug = str(workspace).replace("/", "-")
    briefdir = Path.home() / ".claude" / "projects" / slug / "briefs" / f"probe-{RUN}"
    briefdir.mkdir(parents=True, exist_ok=True)
    briefdir_file = briefdir / "000-premises.md"
    briefdir_file.write_text(f"BRIEFDIR_CANARY={BRIEFDIR_CANARY}\n", encoding="utf-8")

    READABLE[str(outside_file)] = "outside"
    READABLE[str(briefdir_file)] = "briefdir"

    @tool(
        "report",
        "Report one probe answer. Call once per question.",
        {"who": str, "question": str, "answer": str},
    )
    async def report(args: dict[str, Any]) -> dict[str, Any]:
        observed["reports"].append(
            {
                # Claimed by the model, from its own AgentDefinition prompt.
                "who": str(args.get("who", "")),
                "question": str(args.get("question", "")),
                "answer": str(args.get("answer", "")),
                # Supplied by the gate. Kept beside `who` rather than instead of it:
                # agent_id is opaque, and the two disagreeing is itself a finding.
                "agent_id": args.get(STAMP),
            }
        )
        return {"content": [{"type": "text", "text": "Recorded."}]}

    async def pre_tool_use(
        hook_input: dict[str, Any], tool_use_id: str | None, _context: Any
    ) -> dict[str, Any]:
        name = str(hook_input.get("tool_name") or "")
        agent_id = hook_input.get("agent_id")
        tool_input = hook_input.get("tool_input") or {}

        if name == REPORT_TOOL:
            stamped = dict(tool_input)
            stamped[STAMP] = agent_id or "root"
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": stamped,
                }
            }

        if name in SPAWN_TOOLS:
            observed["spawns"].append(
                {
                    "agent_id": agent_id,
                    "tool_use_id": tool_use_id,
                    "subagent_type": tool_input.get("subagent_type"),
                    "prompt": tool_input.get("prompt"),
                    "leaked": _leaked(json.dumps(tool_input, default=str)),
                }
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }

        if name == "Read":
            # Allowed only for the two paths question 5 is about, matched exactly.
            # Recorded either way: an attempt on some other path is a worker going
            # looking, and that is worth seeing rather than silently refusing.
            path = str(tool_input.get("file_path") or "")
            allowed = path in READABLE
            observed["reads"].append(
                {
                    "agent_id": agent_id,
                    "target": READABLE.get(path, "(not a probe target)"),
                    "file_path": path,
                    "allowed": allowed,
                    "tool_use_id": tool_use_id,
                    "response": None,
                    "canary_in_response": None,
                }
            )
            if allowed:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                    }
                }

        # Everything else. The denial is not a policy test -- it is what makes the
        # canaries mean something, because a worker that could Read the workspace
        # would report MEMORY_CANARY whether or not project memory reached it.
        observed["denied"].append({"tool_name": name, "agent_id": agent_id})
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "Denied by the probe. Answer from your own instructions only, "
                    "using the report tool. Do not retry."
                ),
            }
        }

    async def post_tool_use(
        hook_input: dict[str, Any], tool_use_id: str | None, _context: Any
    ) -> dict[str, Any]:
        """
        Close question 5 on the wire.

        STYLE.md §2: a probe captures the result, not the narration. Whether a worker
        *says* the read failed is a sentence about a tool call; `tool_response` is what
        the tool returned. The two disagreeing is itself a finding, so both are kept
        and neither is derived from the other.
        """
        if str(hook_input.get("tool_name") or "") != "Read":
            return {}
        body = str(hook_input.get("tool_response"))
        for entry in observed["reads"]:
            if entry["tool_use_id"] == tool_use_id and entry["response"] is None:
                entry["response"] = body[:800]
                entry["canary_in_response"] = [
                    c for c in (OUTSIDE_CANARY, BRIEFDIR_CANARY) if c in body
                ]
                break
        return {}

    server = create_sdk_mcp_server("probe", "1.0.0", [report])

    agents = {
        # Two arms differing in one field. Neither is the control for the other's
        # question -- together they say whether `memory` is the lever at all.
        MEM_DEFAULT: AgentDefinition(
            description="Probe worker: default memory.",
            prompt=_worker_prompt(MEM_DEFAULT),
            tools=[REPORT_TOOL],
        ),
        MEM_PROJECT: AgentDefinition(
            description="Probe worker: memory='project'.",
            prompt=_worker_prompt(MEM_PROJECT),
            tools=[REPORT_TOOL],
            memory="project",
        ),
        # tools=None inherits everything the session has, which is the shape
        # templates.FEATURE gives `builder`.
        ROSTER_BROAD: AgentDefinition(
            description="Probe worker: inherits every tool.",
            prompt=_worker_prompt(ROSTER_BROAD, SKILL_PROBE),
        ),
        # An explicit list without SendMessage, which is the shape READ_ONLY_TOOLS
        # gives `reviewer`, `investigator` and `skeptic`.
        ROSTER_NARROW: AgentDefinition(
            description="Probe worker: explicit tool list, no SendMessage.",
            prompt=_worker_prompt(ROSTER_NARROW),
            tools=[REPORT_TOOL, "Read", "Glob", "Grep"],
        ),
        # Question 5. Read is granted here; the gate is what bounds it to two paths.
        PATH_READ: AgentDefinition(
            description="Probe worker: reads two absolute paths outside cwd.",
            prompt=PATH_READ_TASK.format(
                report=REPORT_TOOL,
                name=PATH_READ,
                outside=outside_file,
                briefdir=briefdir_file,
            ),
            tools=[REPORT_TOOL, "Read"],
        ),
        # Question 6. The only role carrying initialPrompt, and INITIAL_CANARY exists
        # nowhere else -- not on disk, not in any other prompt, not in the lead's
        # context -- so a report of it has exactly one possible source.
        INIT_SEED: AgentDefinition(
            description="Probe worker: initialPrompt is set.",
            prompt=INIT_SEED_TASK.format(report=REPORT_TOOL, name=INIT_SEED),
            tools=[REPORT_TOOL],
            initialPrompt=INIT_SEED_PROMPT,
        ),
    }

    options = ClaudeAgentOptions(
        model=model,
        cwd=str(workspace),
        agents=agents,
        # Mirrors driver.AgentSession._system_prompt: the preset plus an append the
        # lead alone is supposed to see.
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": (
                "You are coordinating a measurement run.\n\n"
                f"BRIEFING_CANARY={BRIEFING_CANARY}\n\n"
                "This value is yours. Never write it into a tool argument except "
                "where you are explicitly told to."
            ),
        },
        # Left unset on purpose, exactly as driver._options leaves it, so the probe
        # measures the configuration the app actually ships.
        permission_mode="dontAsk",
        max_turns=24,
        mcp_servers={"probe": server},
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre_tool_use], timeout=600)],
            # Both events, because a refused read may close through either and
            # question 5's answer is exactly the one that fails.
            "PostToolUse": [HookMatcher(hooks=[post_tool_use], timeout=600)],
            "PostToolUseFailure": [HookMatcher(hooks=[post_tool_use], timeout=600)],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(LEAD_PROMPT)
        async for message in _drain(client):
            if isinstance(message, SystemMessage):
                observed["system_messages"].append(
                    {"subtype": message.subtype, "keys": sorted(message.data.keys())}
                )
                for key in ("memory_paths", "memoryFiles"):
                    files = message.data.get(key)
                    if files is not None and observed["memory_paths"] is None:
                        observed["memory_key"] = key
                        observed["memory_paths"] = files
                if message.subtype == "init":
                    # Recorded per init rather than once. A second init naming a
                    # sub-agent would mean a worker's context is readable off the
                    # wire; one init means the only way to know is to ask it.
                    observed["inits"].append(
                        {
                            "cwd": message.data.get("cwd"),
                            "agents": message.data.get("agents"),
                            "memory_paths": message.data.get("memory_paths"),
                            "skills": message.data.get("skills"),
                            "tool_count": len(message.data.get("tools") or []),
                            "has_skill_tool": "Skill" in (message.data.get("tools") or []),
                        }
                    )
            if isinstance(message, ResultMessage) and message.is_error:
                observed["result_error"] = message.result

    report_findings(workspace)
    # The two temp trees are left for inspection; this one is not, because it sits
    # inside the CLI's own state directory and a probe has no business leaving run
    # nonces there.
    shutil.rmtree(briefdir, ignore_errors=True)
    return 0


async def _drain(client: ClaudeSDKClient, grace: float = 30.0) -> Any:
    """Read until the stream goes quiet -- sub-agents outlive the root's result."""
    queue: asyncio.Queue[Any] = asyncio.Queue()
    done = object()

    async def pump() -> None:
        try:
            async for message in client.receive_messages():
                await queue.put(message)
        finally:
            await queue.put(done)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=grace)
            except TimeoutError:
                return
            if item is done:
                return
            yield item
    finally:
        task.cancel()


def _answers(question: str) -> dict[str, str]:
    return {r["who"]: r["answer"] for r in observed["reports"] if r["question"] == question}


def report_findings(workspace: Path) -> None:
    print(f"\nrun {RUN}   workspace {workspace}")
    print(f"  MEMORY_CANARY   {MEMORY_CANARY}   (only in {workspace}/CLAUDE.md)")
    print(f"  BRIEFING_CANARY {BRIEFING_CANARY} (only in the lead's system-prompt append)")
    for path, target in READABLE.items():
        print(f"  {target:<15} {path}")
    print(f"  INITIAL_CANARY  {INITIAL_CANARY} (only in {INIT_SEED}'s initialPrompt)")

    print("\n=== every report received ===")
    for r in observed["reports"]:
        print(json.dumps(r, indent=2, default=str))
    if not observed["reports"]:
        print("(nothing -- no worker reported, so no question below is answered)")

    leaks = [s for s in observed["spawns"] if s["leaked"]]
    print("\n=== spawn arguments (checked for leakage) ===")
    for s in observed["spawns"]:
        print(json.dumps(s, indent=2, default=str))
    if leaks:
        print(
            "\n!! a canary reached a worker through the lead's spawn prompt. "
            "The arms above are VOID: a worker reporting it proves nothing about "
            "project memory or the briefing."
        )

    memory = _answers("memory")
    briefing = _answers("briefing")
    roster = _answers("roster")

    print("\n=== 1. does project CLAUDE.md reach a worker? ===")
    print(f"  root (control)          {memory.get('root', '(no report)')}")
    print(f"  {MEM_DEFAULT:<24}{memory.get(MEM_DEFAULT, '(no report)')}")
    print(f"  {MEM_PROJECT:<24}{memory.get(MEM_PROJECT, '(no report)')}")
    if memory.get("root") not in (MEMORY_CANARY, None):
        print(
            "  the session itself missed the canary -- the isolation is wrong, and "
            "the worker rows say nothing until that is fixed."
        )

    print("\n=== 2. does the lead's briefing leak to a worker? (expect NONE) ===")
    for name in ROLES:
        print(f"  {name:<24}{briefing.get(name, '(no report)')}")
    if any(briefing.get(n) == BRIEFING_CANARY for n in ROLES):
        print(
            "  a worker saw the lead's append. The canary method cannot "
            "discriminate here, so questions 1 and 3 are VOID."
        )

    print("\n=== 3. does the sibling roster reach a worker? ===")
    for name in (ROSTER_BROAD, ROSTER_NARROW):
        answer = roster.get(name, "(no report)")
        siblings = [r for r in ROLES if r != name and r in answer]
        print(f"  {name:<24}{answer}")
        print(f"  {'':<24}names a sibling: {bool(siblings)} {siblings or ''}")

    print("\n=== 4. did a worker hold the Skill tool? (negative is inconclusive) ===")
    skill_calls = [d for d in observed["denied"] if d["tool_name"] == "Skill"]
    if skill_calls:
        print(f"  yes -- {len(skill_calls)} Skill call(s) reached the gate:")
        for call in skill_calls:
            print(f"    agent_id={call['agent_id']}")
    else:
        print(
            "  no Skill call reached the gate. That is consistent with the worker "
            "not having the tool AND with it having the tool and declining to call "
            "it. This question is not answered."
        )

    print("\n=== 5. can a worker read an absolute path outside cwd? ===")
    if not observed["reads"]:
        print(
            "  no Read reached the gate at all. The question is NOT ANSWERED -- a "
            "worker that never attempted the read and one that could not are the "
            "same row here."
        )
    for target in ("outside", "briefdir"):
        attempts = [r for r in observed["reads"] if r["target"] == target]
        canary = OUTSIDE_CANARY if target == "outside" else BRIEFDIR_CANARY
        said = _answers(target).get(PATH_READ, "(no report)")
        print(f"  {target}:")
        for attempt in attempts:
            got = attempt["canary_in_response"]
            print(f"    tool_response carried the canary: {bool(got)}")
            if not got and attempt["response"] is not None:
                print(f"    tool_response: {attempt['response'][:300]!r}")
            if attempt["response"] is None:
                print("    no PostToolUse arrived for this call -- result unobserved")
        if not attempts:
            print("    not attempted")
        print(f"    worker reported: {said}")
        # The wire and the account are compared rather than one standing in for the
        # other, because a model reporting success is not evidence of success.
        on_wire = any(attempt["canary_in_response"] for attempt in attempts)
        if on_wire != (canary in said):
            print("    !! the worker's answer disagrees with what the tool returned")

    strays = [r for r in observed["reads"] if not r["allowed"]]
    if strays:
        print(f"  {len(strays)} read(s) on other paths, denied:")
        for stray in strays:
            print(f"    {stray['agent_id']}  {stray['file_path']}")

    print("\n=== 6. does AgentDefinition.initialPrompt reach a worker? ===")
    initial = _answers("initial").get(INIT_SEED, "(no report)")
    print(f"  {INIT_SEED:<24}{initial}")
    print(f"  {'':<24}carries the canary: {INITIAL_CANARY in initial}")

    print("\n=== system messages on the wire ===")
    # A tally, not a dump. The first version of this printed every message with its
    # keys; a run emits hundreds of `thinking_tokens` and the answers scrolled off
    # the top, which is a probe that captures the narration instead of the result.
    tally: dict[str, int] = {}
    for message in observed["system_messages"]:
        tally[message["subtype"]] = tally.get(message["subtype"], 0) + 1
    for subtype, count in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {count:>5}  {subtype}")

    print(f"\ninit messages: {len(observed['inits'])}")
    for init in observed["inits"]:
        print(json.dumps(init, indent=2, default=str))
    print(
        f"\nmemory paths (key={observed['memory_key']}): "
        f"{json.dumps(observed['memory_paths'], default=str)}"
    )
    if len(observed["inits"]) <= 1:
        print(
            "only one init -- no per-agent init on the wire, so a worker's context "
            "is not readable except by asking it."
        )

    print("\n=== tool calls denied by the probe ===")
    for call in observed["denied"]:
        print(json.dumps(call, default=str))
    if not observed["denied"]:
        print("(none -- no agent reached for a tool it was not given)")

    if observed["result_error"]:
        print(f"\n!! result error: {observed['result_error']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="claude-sonnet-5")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.model)))
