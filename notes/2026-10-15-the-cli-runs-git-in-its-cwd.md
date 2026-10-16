# Does the Claude CLI run a tree's git configuration when started in it?

## The question

reviewer-10 (rv-final, finding 1) and reviewer-8 (rv-wt-fix-3, finding 1), from a
claude-code docs issue citing the 2.1.78 changelog, say the CLI builds a git-status
section of its system prompt by running `git status` in its own working directory, and
that the CLI process is not inside pptmstr's Bash-only sandbox. If true, launching a
session in a tree a prior autonomous session wrote — the launcher's "continue there"
offer, or resuming a session in its own worktree — runs that tree's git configuration
as the operator. pptmstr sets neither `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS` nor
`includeGitInstructions` (both reviewers grepped), so nothing suppresses it.

This is the execution path the launcher's own pre-press check was moved *after* the
press to avoid (`planning/2026-10-15-ui-rework.md`, round one item 8). Measured before
any fix is chosen.

## The method

`scripts/probe_cli_git_status.py`. It builds, under `$TMPDIR`, reviewer-8's decoy:

- `R`, a throwaway repo with one commit.
- `P`, a linked worktree of `R` (`git worktree add`), so `R` still lists `P` while
  `P/.git` is re-aimed — the launcher's listing is "not evidence" (worktree.py).
- `P/.decoy`, an independent git directory. `P/.git` is overwritten to read
  `gitdir: …/.decoy/.git`, so any git run with cwd `P` uses the decoy's config and
  treats `P` as its working tree.
- A tracked file routed through `filter.x.clean`, committed before the filter is
  defined, then made stat-dirty (`os.utime`) so `git status` must re-read it and run
  the clean filter. Separately, `core.fsmonitor` names a hook program git invokes on
  status. Both the filter and the fsmonitor program `touch` a marker.

The marker is the result; the model's narration is never read or printed (STYLE.md §2).
Four arms, each on its own fresh fixture:

- **a-default** — the CLI as pptmstr launches it today.
- **b-disable-env** — `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1`.
- **c-settings-false** — `{"includeGitInstructions": false}` via `--settings`.
- **d-control-no-cli** — decoy installed, `true` run instead of the CLI; must leave no
  marker.

The CLI arms (a, b, c) need credentials and network that pptmstr's sandbox denies
(`/home/wreel/.claude/.credentials.json` is denied to a sandboxed command), so they are
run by the operator outside the sandbox with:

    .venv/bin/python scripts/probe_cli_git_status.py

The builder ran `--fixture-only` inside the sandbox: the control arm, plus a self-check
that runs plain `git status` in `P` and confirms the marker — proof the decoy is live.

## Results

### Builder-run, inside the sandbox (CLI version 2.1.301)

- **self-check (plain `git status` in P)** — exit 0, filter marker: **YES**,
  fsmonitor marker: **YES**. The decoy fires on a real `git status`; both channels work.
- **d-control-no-cli** — exit 0, filter marker: **no**, fsmonitor marker: **no**. The
  fixture does not fire on its own.

### Operator-run, outside the sandbox (2026-10-16, command cmd-20261016T015757Z-3d6fe79b71b1)

Run by the operator exactly as asked, `.venv/bin/python scripts/probe_cli_git_status.py`,
exit 0 in 10.6 s. The CLI was the SDK's bundled binary, version 2.1.301
(`.venv/.../claude_agent_sdk/_bundled/claude`), which is what pptmstr launches.

| arm | exit | filter | fsmonitor |
|---|---|---|---|
| self-check (git status) | 0 | YES | YES |
| d-control-no-cli | 0 | no | no |
| **a-default** | 0 | **YES** | no |
| b-disable-env | 0 | no | no |
| c-settings-false | 0 | no | no |

**The claim is confirmed.** Started in a tree whose `.git` names a decoy repository, the
CLI ran the decoy's configured clean filter as the operator, outside any sandbox, before
the model ran a turn. It did not run the decoy's fsmonitor, so its git call disables or
bypasses that channel and not the filter. Either documented switch stops it:
`CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1` in the environment, or `includeGitInstructions:
false` in settings. builder-20's caveat about a "no" in arm a does not apply, because arm a
answered yes.

## What each outcome would mean

- **a-default YES** confirms the claim: the CLI runs the tree's git config, unsandboxed,
  at launch. The "continue there" / resume-in-worktree paths then run a session-forged
  tree's program as the operator.
- **a-default no** refutes it: the CLI does not run `git status` (or does not run it in a
  way that triggers the filter/fsmonitor) at launch, and the exposure, as stated, is not
  real for this CLI version.
- **b / c YES while a is YES** means that suppression knob does not actually suppress the
  git run; **no** means it does.

The measurement names the cause and two switches that remove it. What switching it off
costs: a session's system prompt loses its git-status section (branch, recent commits,
working-tree status). An agent can still run `git status` itself, inside its own sandbox
where one applies. Choosing the scope — every session, or only sessions launched into a
worktree or a directory a session may have written — is the operator's call.

## The decision (2026-10-16)

Operator, on the scope: "Every session." Every session pptmstr launches -- new, resumed,
forked, contained or not -- starts its CLI with `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1`
(`driver.GIT_STATUS_OFF`, set as `ClaudeAgentOptions.env` in `AgentSession._options`,
the one place every launch's options are built). **Sessions' system prompts now have no
git-status section**: no branch, recent commits or working-tree status at the start of a
conversation. An agent that wants them runs `git status` itself, inside its sandbox where
it has one. Sub-agents run inside the same CLI process and are covered by the same
variable; so is anything that process spawns, which inherits it.

**Why the environment variable and not `includeGitInstructions: false`.** Read from the
2.1.301 bundle, the CLI decides with the variable first and the setting second:
`let e=<env>.CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS; if(e!==void 0) return !e; return
<settings>.includeGitInstructions ?? true`. So a setting loses to *any* value the
operator's shell exports, whereas `options.env` is merged over the inherited environment
by the SDK transport and replaces it (`tests/test_driver.py`,
`test_the_operators_environment_cannot_switch_git_status_back_on`, through the
transport's real `connect` with the spawn captured). The setting would also have had to
be spliced into `sandbox.containment_settings()`'s string, which the transport passes
verbatim and which tests hold to be verbatim, and it would have given every uncontained
launch a `--settings` flag it does not have today. The variable changes the argv of no
launch (pinned by test).

**What the variable does not settle by itself.** The CLI applies a settings file's own
`env` block to its process environment after launch, so an operator's
`~/.claude/settings.json` could in principle set the variable back. The bundle also has
code that protects a host-spawned variable from settings-file values, but whether it
applies here was read, not measured. Arms g and h of the probe measure it.

### Re-run through pptmstr's own option building (operator, 2026-10-16, cmd-20261016T030405Z-52ce1e8cc65e)

`.venv/bin/python scripts/probe_cli_git_status.py --pptmstr`, exit 0 in 27.6 s, CLI 2.1.301.
Arms a to d repeated their first result.

| arm | expect | exit | filter | fsmonitor |
|---|---|---|---|---|
| e-pptmstr-uncontained | no | 0 | no | no |
| f-pptmstr-contained | no | 0 | no | no |
| g-pptmstr-vs-settings-env (`--settings {"env":{"…":""}}`) | no | 0 | **YES** | no |
| h-settings-env-control (CLI direct, no variable, g's settings) | YES | 0 | YES | no |

**A settings `env` block can switch the section back on over pptmstr's variable** (g YES,
with h YES as its control). So the environment variable alone does not close the
exposure.

**What this run does not settle** (reviewer-16, read-derived; the next probe measures it):
- e and f have no positive control on pptmstr's own path. A "no" there may mean the switch
  works, or that pptmstr's SDK launch never ran git status at all. An arm with pptmstr's
  options and `env={}` is needed, expected YES.
- g tested the `--settings` tier. An attacker who controls a tree writes
  `<cwd>/.claude/settings.json` or `settings.local.json`, and pptmstr does not set
  `setting_sources`, so the CLI loads them. The docs say project and local settings cannot
  set some variables, so the project tier must be measured on its own.
- A forged tree's project settings may also define hooks, which the CLI would run in its own
  unsandboxed process at startup. If they run through pptmstr's launch, that exposure is
  wider than this one.
- reviewer-16 reads Claude Code's env-vars doc as saying the variable also removes the
  built-in commit and PR workflow instructions, not only the git-status snapshot. The
  operator approved "Every session" knowing only the git-status cost.

## What a forged tree's own settings can make the CLI run through pptmstr's launch

Task `cli-trust-probe`. This is a measurement. It proposes no fix, and the fix waits
for its result. The arms are in `scripts/probe_cli_git_status.py` behind `--pptmstr`
(e0 is added there) and `--trust` (p1, p2, k1). Every arm builds its own fixture.

### What the docs say (read 2026-10-16; not measured)

- **`setting_sources`.** The SDK reference (code.claude.com/docs/en/agent-sdk/python,
  `ClaudeAgentOptions`) says: "`None` (CLI defaults: all sources)", and "When
  `setting_sources` is omitted or `None` and `skills` is not set, `query()` loads the
  same filesystem settings as the Claude Code CLI: user, project, and local."
  `AgentSession._options` sets neither field, so a session in P loads
  `P/.claude/settings.json` and `P/.claude/settings.local.json`.
- **Precedence** (code.claude.com/docs/en/settings, "Settings files and precedence"):
  managed, then command line (`--settings`), then project local
  (`.claude/settings.local.json`), then shared project (`.claude/settings.json`), then
  user. "Environment variables aren't a level in this stack ... An `env` block inside
  a settings file is an ordinary key and follows the levels above."
- **Which variables project and local settings may set**
  (code.claude.com/docs/en/settings-reference, "Variables Claude Code ignores in
  `env`"). They "can't set variables that a checked-out repository shouldn't
  control". The list covers the config, temp and home directories, telemetry
  exporters, process wrapper and sync, dialog timers, attachments and file mentions,
  and some launch-environment-only variables. `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS`
  is **not** on it. Per "How `env` values interact with your shell", "A value here
  overwrites the same variable exported in your shell". The exception is that "When
  the Claude Desktop app or a self-hosted environment runner starts the session, the
  launch environment it builds takes precedence instead". "When Claude Code applies
  `env` values" says project and local values apply "after you trust the workspace, or
  at startup in `-p` mode, which never shows the trust dialog".
- **Workspace trust in SDK mode** (code.claude.com/docs/en/permissions, "What runs
  before you trust a folder"). In the column "`claude -p` or the SDK, folder never
  trusted", "Hooks in settings files, the `env` block and helper commands such as
  `apiKeyHelper`" are **Used**. `permissions.allow` and `additionalDirectories` are
  "Not used". "Servers in `.mcp.json`" are "Connected without asking, approved or
  not. The SDK loads them only when `settingSources` includes project settings."
  pptmstr passes `--strict-mcp-config`, which this note has not checked against that
  row.
- **Hooks and the sandbox** (code.claude.com/docs/en/sandboxing, "What runs outside the
  sandbox"): "The sandbox covers shell commands only. Claude's file tools, MCP
  servers, and hooks run outside it", and "command hooks ... run with your full
  access". code.claude.com/docs/en/hooks: SessionStart fires "When a session begins or
  resumes" (matchers `startup`, `resume`, `clear`, `compact`, `fork`), and
  UserPromptSubmit fires "When a prompt is submitted, before Claude processes it".
  The hooks page does not say outright whether SessionStart fires under `-p` or
  stream-json. The k1 controls answer that.
- **Default sandbox writes** (sandboxing, "Default write behavior"): the working
  directory, directories added with `--add-dir`, and "the per-user temp directory that
  `$TMPDIR` points to". That is why the hook's outside marker has to sit outside both.
  The probe flags a contained k1 arm whose fixture lies under `/tmp/claude-<uid>` or
  `$CLAUDE_CODE_TMPDIR`.

The docs therefore predict YES for p1, p2 and every k1 arm, inside and outside the
sandbox. The probe measures whether that holds for the bundled 2.1.301 through
pptmstr's own option building.

### The arms

| arm | launch | planted in P | expect |
|---|---|---|---|
| e0-pptmstr-env-cleared | `AgentSession._options()` with `env={}` | nothing | YES |
| p1-project-env | `_options()` (switch on) | `.claude/settings.json` `{"env":{"…":""}}` | no |
| p1-control | CLI direct, variable removed | same file | YES |
| p1-neutral | `_options()` | `.claude/settings.json` `{"env":{"PPTMSTR_PROBE_NEUTRAL":"1"}}` | no |
| p2-local-env, p2-control, p2-neutral | as p1 | `.claude/settings.local.json` | no / YES / no |
| k1-sessionstart-uncontained | `_options()` | `.claude/settings.json` SessionStart hook: touch `P/HOOK_MARKER_IN`, then `<top>/HOOK_MARKER_OUT` | no |
| k1-sessionstart-contained | `_options()` with `containment_settings()` | same | no |
| k1-sessionstart-control | CLI direct | same | YES |
| k1-userprompt-{uncontained,contained,control} | as above | UserPromptSubmit hook | no / no / YES |

The neutral arms were not asked for. They were added because loading a local
settings file may make the CLI run git itself: the permissions doc says it "runs git
to tell the two apart", meaning a tracked local file from an untracked one. That git
run could fire the decoy and give p2 a YES that the variable did not cause. A neutral
arm that reads no excludes that.

**Inconclusive is printed as such.** A marker that is missing from an arm whose CLI did
not finish reads `inconclusive`, never `no`. That covers SDK exit `-`, and a non-zero
exit from a direct CLI. A marker that is present is YES whatever the exit.

### What each outcome would mean

- **e0 YES**: pptmstr's own launch shape runs `git status`. Then e's and f's "no"
  above show that the switch works. **e0 no** (with a finished CLI): that path never
  ran git, so e and f prove nothing about the switch.
- **p1 YES with p1-control YES and p1-neutral no**: a forged tree's committed
  `.claude/settings.json` turns the git section, and the decoy's filter, back on over
  pptmstr's variable. The environment variable alone does not meet cli-git-off's
  must-not-break property in the case that motivated it. **p1 no with p1-control
  YES**: the project tier cannot override the host-spawned variable, and g's YES is
  specific to the flag tier, which only pptmstr and the operator write. **p1-control
  no**: an empty value from the project tier does not re-enable the section, so p1
  says nothing. **p1-neutral YES**: loading the file runs git by itself, so p1's YES
  is not attributable to the variable.
- **p2**: the same readings for the local tier.
- **k1 uncontained, hook-in YES**: a tree's own hook runs as the operator when pptmstr
  launches into it. That is a wider channel than git status, since it is an arbitrary
  command, and the git switch does nothing about it. **k1 contained, hook-out YES**
  (no caveat printed): the hook ran outside the Bash sandbox, as the docs say.
  **hook-in YES, hook-out no**: the hook ran under a write boundary. **k1 no with its
  control YES**: pptmstr's launch path does not run project hooks for that event.
  **k1-control no**: the event does not fire under `-p`, so that k1 row says nothing.
  SessionStart and UserPromptSubmit are reported separately because the first fires
  at startup and the second needs a prompt to have been sent.

### Results (operator-run 2026-10-16, cmd-20261016T032033Z-c492f25a7ae8, exit 0 in about 1 min, CLI 2.1.301)

`.venv/bin/python scripts/probe_cli_git_status.py --pptmstr --trust --base "$(mktemp -d /tmp/pptmstr-cli-trust.XXXXXX)"`

Arms a–h repeated their earlier results; e0 is new.

| arm | expect | exit | filter | fsmonitor | hook-in | hook-out |
|---|---|---|---|---|---|---|
| e0-pptmstr-env-cleared | YES | 0 | YES | no | | |
| p1-project-env | no | 0 | **YES** | no | | |
| p1-control | YES | 0 | YES | no | | |
| p1-neutral | no | 0 | no | no | | |
| p2-local-env | no | 0 | **YES** | no | | |
| p2-control | YES | 0 | YES | no | | |
| p2-neutral | no | 0 | no | no | | |
| k1-sessionstart-uncontained | no | 0 | no | no | **YES** | **YES** |
| k1-sessionstart-contained | no | 0 | no | no | **YES** | **YES** |
| k1-sessionstart-control | YES | 0 | YES | no | YES | YES |
| k1-userprompt-uncontained | no | 0 | no | no | **YES** | **YES** |
| k1-userprompt-contained | no | 0 | no | no | **YES** | **YES** |
| k1-userprompt-control | YES | 0 | YES | no | YES | YES |

**What it settles:**
- **e0:** pptmstr's own launch path runs git status, so e and f's "no" is the switch working.
- **p1 and p2:** a tree's own `.claude/settings.json` or `settings.local.json` turns the git-status
  section back on over pptmstr's variable. The neutral arms rule out the CLI's own tracked-file
  check.
- **k1:** a tree's own project hooks run, both SessionStart and UserPromptSubmit, when pptmstr
  launches the CLI, uncontained and contained. The hook wrote outside the tree in both. So the
  contained session's sandbox does not cover its hooks, which run in the CLI's process as the
  operator.

So any session that can write `<cwd>/.claude/settings.json` makes the operator run a command of
its choosing, unsandboxed, the next time pptmstr launches or resumes a session in that
directory. The git-status leak is the narrow case, and project hooks are the wide one. The
environment-variable switch does nothing against hooks.

**The operator's answer (dq-5827ae4642b7, 2026-10-16):** "This is an interesting situation. I
think the argument for blocking the settings load wholesale overstates the conflicting nature
of the settings. They are useful, no? I'm curious if this is just a _general_ exposure that
exists in Claude usage regardless of tooling. Lean with 'nothing' changes and think about how
this is different than usual usage." So nothing changes.

The lead's decision text understated the fix's cost. It said CLAUDE.md was unaffected, and that
was wrong. Per the SDK docstring and the Claude Code docs (read by builder-43, not measured), a
user-only `setting_sources` also stops a project's CLAUDE.md, CLAUDE.local.md, .claude/rules,
project skills, commands and agents, and .mcp.json from loading. That strengthens the
operator's answer. What remains open is whether pptmstr lets an agent *write* those files
without a human seeing it, which plain interactive use would show (board task
settings-write-read).

**How this differs from usual Claude Code use (reviewer-22, settings-write-read, read from code
and docs; nothing run):** no rung lets a session write `<cwd>/.claude/settings.json`,
`settings.local.json` or `.claude/hooks/*` without the operator.
- STRICT and PERMISSIVE park every such write.
- Under AUTONOMOUS, pptmstr's own gate *allows* a Write or Edit there (`driver.py:1894-1910, 1950-1956`).
  Only the CLI stops it: its "don't ask" mode denies protected paths (`driver.py:2486`), and its
  sandbox adds a protected-path list that pptmstr's `containment_settings` does not supply
  (`sandbox.py:188-206`). That rests on the CLI's docs, plus one measurement (`settings.local.json`,
  CLI 2.1.292). Nobody has measured `settings.json`, `hooks/*`, Edit, a symlinked route, or 2.1.301.
- Compared with interactive Claude Code, pptmstr is tighter: interactive auto mode sends
  protected-path writes to a classifier, and its sandbox is off by default.
- The real differences: pptmstr never shows the trust dialog, its protection is entirely the
  CLI's and is untested by pptmstr, and AUTONOMOUS runs unwatched.
- Open, from the docs: `<cwd>/sub/.claude/settings.json` is not protected, which matters only if
  a later session is launched in `sub`.

**The cause the measurement names:** the CLI loads the project and local settings tiers for a
session whose cwd is that tree. pptmstr does not set `setting_sources`. A fix, and its cost (a
repo's own `.claude/settings.json` stops applying), is the operator's decision. It is raised as
one.

## The candidate fix: `setting_sources=["user"]`, measured before it is built

Task `setting-sources-probe`. This is a measurement of the fix the operator is deciding
on (dq-5827ae4642b7). It proposes no change to `driver.py`. The arms are in
`scripts/probe_cli_git_status.py` behind `--user-only`, which also runs the `--trust` arms,
since those are its controls.

### What the SDK and docs say "user" alone keeps and drops (read 2026-10-16; not measured)

- **The SDK field.** `ClaudeAgentOptions.setting_sources: list[SettingSource] | None`.
  `SettingSource = Literal["user", "project", "local"]`. In `claude_agent_sdk` 0.2.168, the
  transport's `_build_command` appends `--setting-sources=<comma-joined>` when the list is
  not None. `["user"]` therefore becomes `--setting-sources=user`, and the dry run shows
  exactly that flag added to each arm's argv. The field's docstring says: "Must include
  `"project"` to load CLAUDE.md files." `skills` is unset in pptmstr, so
  `_apply_skills_defaults` leaves the list alone.
- **What each source loads** (code.claude.com/docs/en/agent-sdk/claude-code-features,
  "Control filesystem settings with settingSources"). `"user"`: "User `settings.json`;
  user CLAUDE.md and `~/.claude/rules/*.md`; user skills, commands, and subagents".
  `"project"`: "Project `settings.json` and hooks; project CLAUDE.md and
  `.claude/rules/*.md`; project skills, commands, and subagents". `"local"`:
  "CLAUDE.local.md, `.claude/settings.local.json`".
- **What it does not control** (same page, "What settingSources does not control").
  These are read regardless: managed policy settings; `~/.claude.json` ("Relocate with
  `CLAUDE_CONFIG_DIR` in `env`"); auto memory under `~/.claude/projects/<project>/memory/`;
  claude.ai MCP connectors; and the user file's `sandbox.credentials` deny and mask
  entries.
- **The flag tier.** No page says whether `--setting-sources` affects `--settings`, and
  pptmstr's containment rides on `--settings`. Read from the 2.1.301 bundle (not
  measured), the enabled-sources function adds `"flagSettings"` and `"policySettings"` to
  whatever list it is given, so `--settings` should still load. Arm
  `u-flag-hook-contained` measures this.
- **The docs name this fix.** code.claude.com/docs/en/permissions, "What runs before you
  trust a folder", read in an earlier session's fetch: "Pass `--setting-sources user`, or
  set the SDK's `settingSources` without project settings, so Claude Code reads neither
  the project's settings files nor its `.mcp.json`." That also answers cli-trust-probe's
  open `.mcp.json` question, by the docs.
- **The sandbox** (code.claude.com/docs/en/sandboxing, earlier fetch): "If you exclude a
  source with `--setting-sources` ... Claude Code ignores its `sandbox.filesystem`
  entries, its `Edit` permission rules, and its `Read` deny rules when building the
  sandbox configuration."

**CLAUDE.md under user-only (read, from the docs; not measured).** The user file
`~/.claude/CLAUDE.md` and `~/.claude/rules/*.md` are still loaded. The project files are
not: `<cwd>/CLAUDE.md`, `<cwd>/.claude/CLAUDE.md`, the CLAUDE.md files in parent
directories, and `.claude/rules/*.md`. Neither is `CLAUDE.local.md`. The SDK docstring and
the "CLAUDE.md load locations" table agree on this. **This is the fix's main cost.** A
pptmstr session in a repository, this one included, would no longer see that
repository's CLAUDE.md, project skills, commands or subagents. The probe cannot show
whether CLAUDE.md was read without reading the model's narration, so it is left to the
docs.

### The arms

Every u- arm is pptmstr's options plus `setting_sources=["user"]`. In each pair below,
the argv differs from the control's only by `--setting-sources=user`, which is pinned by
`test_a_user_only_arm_differs_from_its_control_by_the_source_flag_alone`. The control's
measured result is the operator's run above.

| arm | planted | expect | control (default sources, measured) |
|---|---|---|---|
| u0-user-only-env-cleared | nothing, `env={}` | YES (filter) | e0-pptmstr-env-cleared: YES |
| u-p1-project-env | P/.claude/settings.json `{"env":{"…":""}}` | no (filter) | p1-project-env: YES |
| u-p2-local-env | P/.claude/settings.local.json, same | no (filter) | p2-local-env: YES |
| u-k1-sessionstart-uncontained | project SessionStart hook | no (hook-in, hook-out) | k1-sessionstart-uncontained: YES |
| u-k1-sessionstart-contained | same, contained | no | k1-sessionstart-contained: YES |
| u-k1-userprompt-uncontained | project UserPromptSubmit hook | no | k1-userprompt-uncontained: YES |
| u-k1-userprompt-contained | same, contained | no | k1-userprompt-contained: YES |
| u-flag-hook-contained | nothing in P; SessionStart hook added to the containment `--settings` JSON | YES (hook-in) | (positive control) |
| u-user-tier-hook | `CLAUDE_CONFIG_DIR=<top>/config-home`, whose settings.json has a SessionStart hook | YES (hook-in) | (positive control) |

**u0 is what makes u-p1 and u-p2 readable.** With the switch on, a "no" in u-p1 could
mean either that the project env block was not loaded or that this launch shape never
runs git. u0 clears the switch and expects YES.

**u-flag-hook-contained is what makes u-k1 readable**, and it carries the fix's main
risk. If it answers YES, hooks still fire on the user-only shape, so a "no" in u-k1 is
the project tier not loading. It also shows that the flag tier still loads under
user-only, and so the containment still reaches the CLI. If it answers **no** (from a
finished CLI), `--setting-sources=user` drops `--settings`, and so contained sessions
would lose their sandbox. The fix would then be wrong as it stands. This arm shows the
flag file loads. It does not show the sandbox engaged, because that needs a model-run
Bash command.

**u-user-tier-hook does not touch the operator's real `~/.claude`.** `CLAUDE_CONFIG_DIR`
points the CLI at a throwaway directory inside the fixture, which is passed through
`options.env`. The cost is that the CLI also looks for its credentials there, and finds
none. If the operator authenticates through the environment (`ANTHROPIC_API_KEY` or
`CLAUDE_CODE_OAUTH_TOKEN`), the arm runs normally. Otherwise the CLI may fail before
SessionStart fires. A present marker still reads YES whatever the exit, and a missing
one from a failed CLI reads `inconclusive`, never "no". If it reads inconclusive, the
user tier under user-only is unmeasured. The docs table above is then the only evidence.
The probe will not link or copy the real credentials to get a reading.

### What each outcome would mean

- **u0 YES, u-p1 no, u-p2 no**: the project and local `env` blocks no longer reach the
  CLI, and pptmstr's variable holds. **u-p1 or u-p2 YES**: user-only does not exclude
  that tier through the SDK.
- **u-flag-hook-contained YES, every u-k1 no (hook-in and hook-out)**: a forged tree's
  project hooks no longer run through pptmstr's launch, contained or not, and the flag
  tier carrying the containment still loads. **Any u-k1 YES**: that hook event still
  runs, and user-only does not close the hook channel.
- **u-user-tier-hook YES**: the operator's own user-tier settings and hooks still apply
  under the fix.
- **Any row inconclusive**: the CLI did not finish, and the row says nothing.

### Results (operator-run; not yet run)

`.venv/bin/python scripts/probe_cli_git_status.py --pptmstr --trust --user-only --base "$(mktemp -d /tmp/pptmstr-cli-sources.XXXXXX)"`

| arm | expect | exit | filter | fsmonitor | hook-in | hook-out |
|---|---|---|---|---|---|---|
| u0-user-only-env-cleared | YES | not yet run | | | | |
| u-p1-project-env | no | not yet run | | | | |
| u-p2-local-env | no | not yet run | | | | |
| u-k1-sessionstart-uncontained | no | not yet run | | | | |
| u-k1-sessionstart-contained | no | not yet run | | | | |
| u-k1-userprompt-uncontained | no | not yet run | | | | |
| u-k1-userprompt-contained | no | not yet run | | | | |
| u-flag-hook-contained | YES (hook-in) | not yet run | | | | |
| u-user-tier-hook | YES (hook-in) | not yet run | | | | |

## Whether the CLI's protection of `.claude/` holds when pptmstr's gate allows the write

Task `protected-path-probe`. This is a measurement. It proposes no fix: the operator chose
that nothing changes (dq-5827ae4642b7), and this is how pptmstr can tell, per CLI build,
whether the protection the section above rests on still holds. Under AUTONOMOUS the gate
allows a write tool anything under `cwd` (`driver._escapes_write_region`, `_write_region`),
so the only barrier is the CLI's "don't ask" deny and its sandbox's protected-path list.

The arms are in `scripts/verify_gate_hook_write.py`. Each runs one contained session
through `AgentSession` in its own fresh `git init` tree, and every verdict comes from the
host's read of the file after the session, with the hooks' record of what the gate
answered. Every arm has a control on each side, using the same tool as its targets. a0 is
the earlier n=1 run (CLI 2.1.292) repeated on the current build.

| arm | policy | call | target | expect |
|---|---|---|---|---|
| a0-write-four | AUTONOMOUS | Write | `.git/hooks/pre-commit`, `.mcp.json`, `.claude/settings.local.json`, `.git/config` | DENIED by the CLI |
| a1-write-settings | AUTONOMOUS | Write | `.claude/settings.json` (absent) | DENIED by the CLI |
| a2-write-hook | AUTONOMOUS | Write | `.claude/hooks/h.sh` (no hooks dir) | DENIED by the CLI |
| a3-edit-settings | AUTONOMOUS | Read, then Edit | existing `.claude/settings.json`; control: Edit of `edit-control.txt` | DENIED by the CLI |
| a4-symlink | AUTONOMOUS | Write | `lnk/settings.json`, `lnk/hooks/h.sh`, with `lnk -> .claude`; host reads `.claude/` | DENIED by the CLI (docs, "Symlinks") |
| a5-bash | AUTONOMOUS | contained Bash | `echo` into existing `.claude/settings.json`, absent `settings.local.json`, `.claude/hooks/h.sh`; `mv .claude .c2-…` | DENIED |
| a5-bash | AUTONOMOUS | contained Bash | `sub/.claude/settings.json` | open: the docs cover cwd and its parents, not subdirectories |
| a6-strict-approved | STRICT | Write, approved by the probe in the queue | `.claude/settings.json` | DENIED by the CLI after the approval |

**What each outcome would mean.** DENIED "by the CLI, after the gate allowed" is the
protection holding; the printed refusal text says whether the sandbox or the permission
mode refused it. **Any LANDED under AUTONOMOUS** means a session can write its own
`.claude` settings or hooks unattended on this CLI build, and so, per the trust probe
above, choose a command the operator's next launch in that tree runs. **a6 LANDED** means
the CLI does not refuse an operator-approved write there, so the reading in the section
above that "even an approval from the operator's own queue is refused" is wrong. A
`sub/.claude` LANDED matters only if a later session is launched with `cwd` in `sub`.
NOT ATTEMPTED and INVALID say nothing about the path.

### Results (operator-run 2026-10-16, cmd-20261016T123852Z-1061a16567c2, exit 0 in about 2 min, CLI 2.1.301)

Builder-run inside the sandbox, with no CLI: `--offline` built every fixture, the
fixture check passed for each, and the gate's offline answer was `allow` for every
AUTONOMOUS target, `Bash` included, and `require_approval` for a6.

`.venv/bin/python scripts/verify_gate_hook_write.py --base "$(mktemp -d /tmp/pptmstr-protected-path.XXXXXX)"`

Every control write (control-first, control-last, edit-control) LANDED in its session.

| arm | target | verdict | gated by |
|---|---|---|---|
| a0-write-four | `hooks/pre-commit` | DENIED | CLI, after the gate allowed |
| a0-write-four | `.mcp.json` | DENIED | CLI, after the gate allowed |
| a0-write-four | `.claude/settings.local.json` | DENIED | CLI, after the gate allowed |
| a0-write-four | `.git/config` | DENIED | CLI, after the gate allowed |
| a1-write-settings | `.claude/settings.json` | DENIED | CLI, after the gate allowed |
| a2-write-hook | `.claude/hooks/h.sh` | DENIED | CLI, after the gate allowed |
| a3-edit-settings | `.claude/settings.json` (Edit) | DENIED | CLI, after the gate allowed |
| a4-symlink | `lnk/settings.json` | DENIED | CLI, after the gate allowed |
| a4-symlink | `lnk/hooks/h.sh` | DENIED | CLI, after the gate allowed |
| a5-bash | `.claude/settings.json` | DENIED | CLI, after the gate allowed |
| a5-bash | `.claude/settings.local.json` | DENIED | CLI, after the gate allowed |
| a5-bash | `.claude/hooks/h.sh` | DENIED | CLI, after the gate allowed |
| a5-bash | `sub/.claude/settings.json` | DENIED | CLI, after the gate allowed |
| a5-bash | `mv .claude` | DENIED | CLI, after the gate allowed |
| a6-strict-approved | `.claude/settings.json` (approved) | DENIED | CLI, after the gate allowed |

**What it settles:** on CLI 2.1.301 no route measured lets a session write its own `.claude`
settings or hooks, `.mcp.json` or `.git` config. That covers Write, Edit, a symlinked folder,
sandboxed Bash (including the `sub/.claude` case the docs left open, and a rename of the folder),
and STRICT with the gate approving. In every case the CLI refused after pptmstr's gate allowed.
So the CLI's protection holds today, and it remains the only protection: pptmstr enforces none of
it, and this probe is how pptmstr checks it after a CLI change. n = 1 session per arm, one model,
one CLI build, one tree layout. The a5 refusals do not say whether the sandbox or the permission
mode refused (builder-46's caveat).
