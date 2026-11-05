# Probe: a skill shipped inside the pptmstr package, beside the user's and project's own

Board task `skill-probe`, 2026-10-21. The operator's requirement: pptmstr's own
skills and plugins ship as part of the application, need no preparation in any
project, and coexist with the user's and the project's existing configuration
and skills directories.

Nothing in `pptmstr/`, `~/.claude`, this worktree's `.claude/` or the planning
record was written. No fix is declared.

**Recommendation.** Ship the skill as a plugin directory inside the package,
`pptmstr/plugins/pptmstr/`. Resolve it with `importlib.resources` and pass its
absolute path as `ClaudeAgentOptions.plugins`. Leave `skills` and
`setting_sources` unset. That meets both requirements. It is measured to load
next to the bundled skills and the user's synced skills, and to ship in the
wheel and the sdist. Two things have to change with it before it does anything
in a pptmstr session:

- **The gate.** `approval.classify` parks a `Skill` call on every policy.
  Headless and `AUTONOMOUS` sessions turn that into a refusal.
- **Workers with a tool list.** They get no skill unless the role says so.

Costs and the open choices are at the end.

## How it was run

Script: `scripts/skill_probe.py`. It builds stub plugins and a stand-in project
under `--out`, runs each scenario through `ClaudeSDKClient` in pptmstr's own
shape (`permission_mode="dontAsk"`, `strict_mcp_config=True`, and a
`PreToolUse` hook that records each call and allows only `Skill`/`Agent`), and
writes `results.json`. Without `--live`, a scenario stops at the
`system`/`init` frame. The CLI emits that frame before any API request, so
these scenarios need no login.

- claude-agent-sdk 0.2.168, bundled CLI `2.1.301 (Claude Code)`, model `haiku`.
- **Run 1 (sandbox, init only):**
  `.venv/bin/python scripts/skill_probe.py` gives
  `$TMPDIR/skill-probe/results.json`.
- **Run 2 (operator, live).** The sandbox cannot read `~/.claude/.credentials.json`,
  so every live turn there answers `Not logged in · Please run /login`
  (measured). It was run by the operator as command
  `cmd-20261021T171105Z-ba3cb4bb3e64`:
  `.venv/bin/python scripts/skill_probe.py --live --out shots/skill-probe`,
  giving `shots/skill-probe/results.json` and `shots/skill-probe.log`
  (`/shots/` is git-ignored). The 19 scenarios cost $0.0165 in total.
- **Run 2's F and L4 are void.** `--out` was relative, and the CLI resolves a
  relative `--plugin-dir` against the session cwd. Those two scenarios move the
  cwd into the stand-in project, so the plugin did not load. The init frame
  says so:
  `plugin_errors: [{plugin: "inline[0]", type: "path-not-found", path: ".../standin-project/shots/skill-probe/pptmstr-plugin"}]`.
  The script now resolves `--out`. Those scenarios were asked again as
  `cmd-20261021T171530Z-e3ca78309651`; see "Run 3" below.

Stub layout, from the plugin manifest reference
(https://code.claude.com/docs/en/plugins-reference, "Standard layout" and
"Manifest file", read 2026-10-21):

    pptmstr-plugin/
      .claude-plugin/plugin.json   {"name": "pptmstr", "version": "0.0.0-probe", ...}
      skills/pptmstr-figures/SKILL.md   (frontmatter name + description, a marker body)

`claude plugin validate $TMPDIR/skill-probe/pptmstr-plugin` reports
`✔ Validation passed`. According to the reference, the manifest is optional:
without one, the plugin is named after its directory. Scenario I confirms
that, as `pptmstr-nomanifest:pptmstr-figures`. Every component is namespaced
`plugin:skill`.

## 1. A packaged plugin

| Scenario | Init `skills` (relevant) | Init `plugins` | Source |
|---|---|---|---|
| A, no plugin | 32 skills, none `pptmstr` | built-ins only | run 2 |
| B, `plugins=[stub]` | adds `pptmstr:pptmstr-figures` (33) | `{name: pptmstr, source: pptmstr@inline, version: 0.0.0-probe}` | runs 1 and 2 |

- **The skill is listed, under the plugin's namespace**, as
  `pptmstr:pptmstr-figures`. `SystemInitPlugin` carries the absolute path and
  `source: "pptmstr@inline"`. `plugin_errors` is absent on a clean load and
  names the failing path when one fails (run 2, F).
- **The model invokes it on a drawing prompt.** In L2, the prompt "Draw me a
  dependency graph of three packages…" produced one call,
  `Skill {"skill": "pptmstr:pptmstr-figures", "args": "Dependency graph: …"}`,
  and the reply quoted the body's marker. L4b did the same. That is two samples
  on haiku, not a rate.
- The model treated the stub's marker instruction ("reply only with …") as an
  injection and ignored it, but quoted it. So the body reached the model. A
  real skill body is guidance, not an instruction to answer in a fixed way.
- The SDK passes each `plugins` entry as `--plugin-dir <path>`
  (`_internal/transport/subprocess_cli.py`, the `# Add plugin directories`
  block).

## 2. Coexistence

**The user's own skills.** `~/.claude/skills/` was listed, read-only. It holds
only `synced/<bucket>/`, the 13 `anthropic-skills:*` skills, and no
hand-written skill. So "the user's own" was tested with the synced set.

| Scenario | Count | `anthropic-skills:*` | Bundled (`dataviz`, …) | Plugin skill |
|---|---|---|---|---|
| A, default (run 2) | 32 | 13 | yes | — |
| B, plugin + default | 33 | 13 | yes | yes |
| E, plugin + `setting_sources=[]` | 20 | **0** | yes | yes |

- With `plugins=` set and `setting_sources` left at its default, the user's
  synced skills and the bundled ones all still load. The plugin only adds.
- **`setting_sources=[]` (SDK isolation) drops the user's skills** but keeps
  the plugin and the bundled set. Isolation is not a way to meet the
  coexistence requirement.
- In run 1 (sandbox, no login), A listed 18 skills: no `anthropic-skills:*`,
  and no `schedule`. The synced set therefore depends on the login or on
  something the sandbox denies. Which one was not isolated.

**`skills=[…]` restricts. It does not add.**

| Scenario | What the model listed (L-runs, live) |
|---|---|
| L1, `skills=None` | `pptmstr:pptmstr-figures`, `dataviz`, … every `anthropic-skills:*` |
| L3, `skills=["pptmstr:pptmstr-figures"]` | `pptmstr:pptmstr-figures` and nothing else |
| L3b, `skills=["pptmstr-figures"]` (bare) | `pptmstr:pptmstr-figures` and nothing else |
| L3c, `skills="all"` | same as L1 |

- The init frame's `skills` list does **not** show the filter: C, C2 and D
  list the same 33 as B. The model's listing does (L3). Do not read the
  filter's effect off the init frame.
- A bare name matched the namespaced plugin skill (L3b).
- **Setting `skills` to anything also changes which settings load.** Argv,
  built with the SDK's own `_build_command` (run):

      None                         -> no --allowedTools, no --setting-sources
      ['pptmstr:pptmstr-figures']  -> --allowedTools Skill(pptmstr:pptmstr-figures)  --setting-sources=user,project
      'all'                        -> --allowedTools Skill                           --setting-sources=user,project

  `--setting-sources=user,project` drops `local`, so the project's
  `.claude/settings.local.json` stops loading. That is a coexistence
  regression, independent of the filter (`_apply_skills_defaults`). The SDK
  docstring warns (`types.py`, `skills`) that `"all"` puts a bare `Skill` in
  `allowedTools`. Under pptmstr's `dontAsk` + `PreToolUse` gate, the hook
  still runs first, so the gate keeps its say. That was read, not run against
  pptmstr's driver.

**Name collisions.**

- *Plugin against a same-named user or project skill.* The skills page
  (https://code.claude.com/docs/en/skills, conflict table, read 2026-10-21):
  "A plugin skill and a skill at any of the locations above | Both load,
  because plugin skills are namespaced as `/plugin-name:skill-name`". Run 1, F
  (a project skill `pptmstr-figures` in a stand-in project, plus the plugin)
  lists both, `pptmstr-figures` and `pptmstr:pptmstr-figures`. Neither
  overrides the other. **Which one gets used is the model's choice, and in
  run 3 it chose the user's skill.** In L4, re-run with an absolute plugin
  path, init listed both. The prompt "Draw me a dependency graph…" produced
  `Skill {"skill": "pptmstr-figures"}`, the project's bare one. The reply
  quoted `SKILL PROJECT LOADED`. That is one sample. It is the reason the
  always-on floor should name the namespaced skill (design, step 4).
- *Two plugins with the same skill name* (H): both load,
  `pptmstr:pptmstr-figures` and `rival:pptmstr-figures`.
- *Two plugins with the same plugin name* (G, a stand-in for a user who
  installed something else called `pptmstr`): init lists both plugins but only
  one `pptmstr:pptmstr-figures`. In L4b the invoked body was the first one
  passed (`SHIPPED`). One sample. Whether "first wins" is a rule was not
  established.
- No user skill was written to `~/.claude` to test this; the stand-in project
  and the second plugin are the simulation the task asked for.

## 3. The gate

`approval.classify("Skill", …)`, run against both a plugin skill and `dataviz`:

    STRICT     -> REQUIRE_APPROVAL
    PERMISSIVE -> REQUIRE_APPROVAL
    AUTONOMOUS -> REQUIRE_APPROVAL

`Skill` is in none of `_AUTO`, `_BUS_AUTO` or `_REVIEW`, so it reaches the
fail-closed fallthrough ("Unknown, including every MCP tool this build has never
seen"). In `driver.py`'s refusal-reason method (the one returning "needs approval
and this session is running unattended by choice"), the call is then:

- interactive, `STRICT`/`PERMISSIVE`: parked in front of the operator, on every
  drawing reply;
- `AUTONOMOUS`: refused with "the calls that still need approval are the ones
  this build does not recognise". This session's own `Monitor` call was refused
  with exactly that text;
- headless: refused, "needs approval and no operator is attached".

A shipped skill is therefore dead weight or a nag until `classify` admits it.
The narrow form would admit `Skill` only when `tool_input["skill"]` starts with
`pptmstr:`, because that is pptmstr's own text, from its own package. Every
other skill keeps falling through. The reason not to admit `Skill` wholesale:
a user or project skill may carry `` !`command` `` lines. Per the skills page,
these "run shell commands before the skill content is sent to Claude" and are
checked against permission rules. **Unverified:** whether pptmstr's
`PreToolUse` hook sees those commands at all. That was not run.

**Does loading a plugin spawn anything?** At the init frame, every live
descendant of the CLI was listed from `/proc`:

- run 1: one zombie `sh` (`state=Z`) in every scenario, including A with no
  plugin;
- run 2: none.

A plugin with only a skill starts no process. What can start one is a plugin's
`.mcp.json`/`mcpServers` (stdio), `hooks`, `lspServers` or `monitors` (plugins
reference, "Standard layout"). That is the same outside-the-sandbox spawn that
`sandbox.py` records for stdio MCP servers. `strict_mcp_config=True` was set in
every scenario. Whether it also keeps out a *plugin's* MCP servers was **not
tested**. The shipped plugin should carry skills only, and a test should hold it
to that.

## 4. Packaging

`[tool.hatch.build.targets.wheel] packages = ["pptmstr"]`, and the sdist
`include` lists `/pptmstr`. The tree was copied to `$TMPDIR` with the stub at
`pptmstr/plugins/pptmstr/` and built with hatchling 1.32.4. Hatchling is not
in the venv; a copy already present at `/tmp/claude-1000/hl` was used.

    wheel: ['pptmstr/plugins/pptmstr/.claude-plugin/plugin.json',
            'pptmstr/plugins/pptmstr/skills/pptmstr-figures/SKILL.md']
    sdist: ['pptmstr-0.1.0/pptmstr/plugins/pptmstr/.claude-plugin/plugin.json',
            'pptmstr-0.1.0/pptmstr/plugins/pptmstr/skills/pptmstr-figures/SKILL.md']

Both include the dot-directory `.claude-plugin/` with no configuration change.
The wheel was then installed with `pip install --no-deps --target`, and:

    files('pptmstr')/'plugins'/'pptmstr' -> PosixPath .../site/pptmstr/plugins/pptmstr
    as_file(...) -> same path; plugin.json and SKILL.md both is_file() True

A session given that installed path lists `pptmstr:pptmstr-figures`, with no
`plugin_errors`. The path passed must be absolute; run 2's F shows what a
relative one does. `as_file` is the right call, because it also covers a
zipped install by extracting. `--plugin-dir` also accepts a `.zip`
(`claude --help`).

## 5. Workers

Runs 2 and 3, sub-agent `drawer` (haiku) launched through the `Agent` tool:

- **L5, `tools=["Read"]`, `skills=["pptmstr:pptmstr-figures"]`, run twice.**
  Both times the worker's first line was `SKILL SHIPPED LOADED`, and the hook
  saw only the lead's `Agent` call. So `AgentDefinition.skills` **preloads the
  skill's whole body into the worker's context at start**. It does not make
  the skill callable on demand. The skills page agrees:
  `disable-model-invocation` "also stops the skill from being preloaded into
  subagents". The two runs disagree on the listing. In run 2 the worker
  printed a full catalogue. In run 3 it said "I have no view of an
  available-skills listing". So no conclusion is drawn about the listing.
- **L6, `tools=["Read"]`, no `skills`.** The worker said: "My context has no
  available-skills listing, and no Skill tool is among the tools I have."
- That is pptmstr's case for every role with a tool list. `_team` passes
  `role.tool_list(...)`, and `READ_ONLY_TOOLS` (the reviewer roles) has no
  `Skill`.
- **L7, `tools=None` (inherit), no `skills`, run 3.** The worker made its own
  call, `Skill {"skill": "pptmstr:pptmstr-figures"}`, and the reply quoted
  `SKILL SHIPPED LOADED`. pptmstr's `PreToolUse` hook received that call with
  the worker's `agent_id` (`aeb4c3bd49bbca89b`). So a worker that inherits its
  tools invokes the skill on demand, and the gate sees and attributes the
  call like any other sub-agent call.

So a worker with a tool list has two options:

1. `skills=[...]`: the body, always on, per worker. This is the cost the
   probe is meant to avoid, but bounded to roles that draw.
2. `"Skill"` added to the role's tools, which is the on-demand form L7 shows
   for inherited tools. The SDK calls this form deprecated (`types.py`,
   `AgentDefinition.tools` comment). It was not run with an explicit list.

## Run 3

Operator command `cmd-20261021T171530Z-e3ca78309651`, exit 0 in 43.8 s:
`.venv/bin/python scripts/skill_probe.py --live --out shots/skill-probe-2 --only F-plugin-and-same-named-project-skill L4-live-draw-collision L7-live-subagent-inherits-tools L5-live-subagent-skills`,
giving `shots/skill-probe-2/results.json`. The plugin path was absolute. F
and L4 loaded both skills, with no `plugin_errors`. Its results are folded into
§2 and §5 above.

## Recommended design, and its cost

1. **Ship** `pptmstr/plugins/pptmstr/.claude-plugin/plugin.json`
   (`name: "pptmstr"`) and `skills/figures/SKILL.md`. The skill is listed as
   `pptmstr:figures`. Its body carries the TeX use/avoid list, the SVG design
   rules and the Mermaid-over-SVG choice. No hooks, MCP, LSP, monitors or `bin/`.
2. **Load** it in `driver._options` with
   `plugins=[{"type": "local", "path": str(<as_file(files("pptmstr") / "plugins" / "pptmstr")>)}]`
   on every launch, fresh or resumed. Leave `skills=None` and
   `setting_sources=None`. Then the user's, project's and local settings and
   skills load exactly as they do today (measured for user/bundled skills;
   `local` is read from the SDK's argv builder).
3. **Admit** `Skill` for `pptmstr:`-prefixed names in `approval.classify`, on
   every rung. That is a gate decision, so it belongs on the record, not in
   this probe.
4. **Keep a floor** in `fences.session_prompt()`: which fences render, and
   "before drawing, use the `pptmstr:figures` skill". The second sentence names
   the namespaced skill, so a user's same-named skill cannot quietly take its
   place.
5. **Workers that draw.** A role that inherits its tools needs nothing (L7). A
   role with a tool list (`READ_ONLY_TOOLS`) needs either `"Skill"` in the
   list, which is on demand but deprecated and not run, or `skills=[...]`,
   which is preloaded (L5). The reviewer roles do not draw figures, so leaving
   them without the skill is a defensible default.

**Cost.**

- *Always on.* `claude --plugin-dir <stub> plugin details pptmstr` projects
  "Always-on: ~43 tok added to every session" for the stub's description. The
  fence guidance it replaces is 1,968 characters today, measured with
  `len(fences.session_prompt())`. Read as tokens that is roughly 450–500, but
  that conversion is estimated, not measured. The floor stays, so the saving
  is that figure minus the floor.
- *Per drawing reply.* One extra tool round trip, plus the body. On haiku, L2
  took 3 turns and $0.0028 against L1's 1 turn and $0.00046. Those were
  different prompts, so this shows the shape of the cost, not its size.
- *Behaviour risk.* The guidance stops being guaranteed. The model has to
  choose to call the skill. It did in both drawing samples (L2, L4b), which is
  not a rate. With a same-named project skill present, it called that one
  instead (L4, run 3). A session that does not call pptmstr's skill draws
  without the rules, so the floor has to carry whatever must never be missed.
- *Code.* One path helper, one `plugins=` line, one `classify` branch with its
  tests, a test that the shipped plugin has no spawning components, and a
  decision per drawing role.
