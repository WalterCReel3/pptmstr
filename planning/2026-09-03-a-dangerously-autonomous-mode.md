# A dangerously autonomous mode is measured by git, not by the gate

**Dated:** 2026-09-03 · **Updated:** 2026-09-04 · **Status:** scoped; `Bash` auto-approval is
decided, the containment layer is chosen and blocked on one probe (§8a.1), the spawn rule and the
git sensor's build order are open ·
**Origin:** an operator request for an experimental under-gated mode, following research into
the July 2026 OpenAI/Hugging Face incident recorded in `notes/2026-09-03-what-the-incident-corroborates.md`

The request is a mode or team that is dangerously autonomous, including auto-approved `Bash`.
The danger is the chosen property, not a cost to be minimised — this record does not argue against
it. What it does is establish what stops working when the gate stops running, and where each
displaced function has to go instead.

The short version: **the gate is the sensor, so an under-gated mode has no sensor — unless the
measurement moves to git, which is the one instrument that observes writes without the gate's
participation.**

---

## 1. Turning the gate off unsolders the instrument

Verified by execution, this session:

- `PendingApproval` is constructed in exactly one place outside `fake_driver.py`:
  `pptmstr/driver.py:1363`, inside `_park`.
- `pptmstr/driver.py:1225` returns `_allow_with(...)` on `Disposition.AUTO_APPROVE` and returns
  before reaching the `_park` call at line 1238.
- `writes=` appears once in `pptmstr/store.py`, at line 434 in the `ApprovalResolved` arm. It is
  the sole writer of `Task.writes`. `Task.wrote_outside_declaration` and
  `Snapshot.unattributed_writes` derive from that field alone.

So auto-approving the writing tools makes `Task.writes` identically `ApprovedWrites()` for every
task in the run. Items 1 and 2 of `planning/2026-09-01` — built, with `wrote_outside_declaration`
cases in `tests/test_store.py` — read zero, indistinguishable from a session that wrote nothing.
`planning/2026-08-11` recorded the mechanism (*"Auto-approved calls leave no approval record.
AUTO_APPROVE returns immediately and emits nothing."*); 09-01 then built the measurement on that
record. Neither is wrong; nothing had put them side by side.

**`Bash` makes this permanent rather than fixable.** `model.written_path` (`model.py:855`):

> **None is not evidence that nothing is written.** `Bash` names no path and can write anything; a
> heredoc is invisible here and always will be. […] a clean record over a session of `Bash` calls
> has earned nothing.

An observation record on the auto-approve path would recover the measurement for
`Write`/`Edit`/`MultiEdit`, because those calls name their file. It recovers nothing for `Bash`,
because there is no path to extract. In a mode where `Bash` runs unattended, the approval-derived
instrument is not degraded — it is inapplicable, and a zero from it means nothing at all.

---

## 2. Where the measurement goes instead

`notes/2026-08-31`'s thesis is that pptmstr's asset is being the only participant that sees both
what was declared and what was done. Under this mode pptmstr still sees the declaration —
`Task.touches` is recorded at `declare_task` time and is unaffected by the gate. What it loses is
the second half.

**Git is the second half, and it observes writes without the gate's participation.** `git status`
and `git diff --numstat` against the session's `cwd` measure what was actually written, including
every heredoc, every `sed -i`, every file created by a script the agent wrote and ran. That is
strictly more coverage than `ApprovedWrites` has ever had, and it is the only instrument that
survives `Bash` auto-approval.

Consequences worth stating before building:

- **The comparison stays the same shape, and gets a better denominator.** Declared `touches`
  against git's actual write set is the same divergence reading Item 3 of `planning/2026-09-01`
  specifies, sourced differently. It answers the question `Task.writes` was built to answer and
  answers it for `Bash` too.
- **Attribution is what git cannot do.** One tree, N agents, no per-write author. Git gives the
  union; it cannot say which node wrote which file. Sequencing helps (sample between tool calls)
  and does not solve it. `AgentRecord.cwd` per node, or a worktree per node, is the only clean
  answer, and worktree-per-node is a real design with a real cost.
- **Untracked files are the gap, and this project already knows it.** `CLAUDE.md`: *"git add a
  file as soon as you create it — an untracked file another agent overwrites has no revert path."*
  Under this mode that rule stops being hygiene and becomes the precondition for both measurement
  and reversibility.
- **This is an imperative shell concern, not a reducer concern.** Running git is IO. It belongs
  beside `Transcript` and `Bridge` on `STYLE.md` §1's exception list, with its own reason written
  down, and it must not go on the frame path.

**The sampling trigger works in a solo session — measured, 2026-09-04.**
`scripts/verify_root_tool_bracket.py` settles the design's declared weakest step. A root-session
tool call **does** get a closing hook: three of three paired exactly by `tool_use_id`, with the
failing `Bash` arriving as `PostToolUseFailure` and the two successes as `PostToolUse`. The
sub-agent control arm fired identically and carried an `agent_id` on every event, so the root-side
result is a finding rather than a dead harness.

The closing hooks for root calls arrive with **no `agent_id`**, which confirms
`_pre_tool_use`'s comment that it is present only for sub-agent calls. Two consequences:

1. `driver._post_tool_use` drops them at its first line (`if not agent_id: return {}`), and
   `_pre_tool_use` opens no bracket for them. That is deliberate, and it means **the sensor must
   keep its own bracket rather than reusing `_subagent_in_flight`**, which is sub-agent-only by
   construction.
2. Absence of `agent_id` is not a missing attribution — `NodeId` is `tuple[str, str | None]`, so
   `(session_id, None)` *is* the root node. A solo run attributes to exactly one node, which is
   the condition a first dangerously-autonomous run will be in.

Combined with §8c's finding that a sandbox violation closes through `PostToolUseFailure` rather
than `PostToolUse` — and that under this mode a large share of `Bash` calls will be violations —
**the trigger must register both closing hooks.** Watching `PostToolUse` alone would stop sampling
at the moment containment starts working, and would under-report by one call in three even in the
uncontained arm measured here.

---

## 3. What auto-approving `Bash` actually removes

Stated as facts, so the mode is priced rather than discovered.

**Egress cannot be denied.** `curl`, `wget`, `git push`, `ssh`, `pip install` are all `Bash`. The
earlier framing of this preset as the mirror of `RESEARCH` — mutation-permitted / egress-denied —
is dead. Removing `WebFetch` and `WebSearch` from `Role.tools` is cosmetic once `Bash` runs
unattended: it changes which tool the model reaches for, not what the process can do.

**The whole irreversible category goes at once.** `notes/2026-08-31` §6.7 keeps irreversible
operations gated by category — *"network calls, messages to people, destruction of untracked
files, migrations"*. Every one of those is reachable from `Bash`. There is no partial version of
this: auto-approving `Bash` auto-approves the category.

**The gate cannot narrow it.** `approval.classify(tool_name, tool_input)` decides by tool class.
Content-classifying `Bash` is 08-11 §2's parsing problem in which the parser is the security
property, and the model-judges-its-own-call route is refused by
`planning/2026-08-11-research-phase-auto-approval.md` on the grounds that the thing being gated
cannot be the thing that decides whether it needs gating. Neither route is open, which is why
containment has to move below the gate (§4).

**The agent inherits the operator's credentials, and this is the incident's actual analogue.**
The July 2026 agents did not breach Artifactory — they were *given* shared credentials for a
legitimate purpose and built a coordination channel with them, with no exploit
(`notes/2026-09-03-what-the-incident-corroborates.md` §2). A `Bash`-autonomous agent on this
desktop holds the operator's SSH keys, `gh` token, `~/.claude` credentials, and whatever cloud
profiles are in the environment. That is the same shape and it is the one axis of the incident
that genuinely transfers here — the research established that isolation and blast radius did *not*
transfer to mainline pptmstr, and this mode is precisely the change that makes them transfer.

---

## 4. Containment is mostly already built, and is reached per launch

Since the gate cannot contain and `Role.tools` cannot contain, containment is a property of how
the CLI subprocess is launched. Two agents researched this independently against the installed
SDK source and the current upstream docs; the second re-read every mechanical claim of the first
and confirmed each one.

**What pptmstr controls today.** `AgentSession._options` sets `model`, `cwd`, `resume`,
`session_id`, `agents`, `system_prompt`, `permission_mode="dontAsk"`, `include_partial_messages`,
`thinking`, `mcp_servers`, `hooks`. It sets nothing about the process boundary — not `env`,
`settings`, `sandbox`, `user`, `stderr`, `cli_path`, or the tool lists.

**A scrubbed environment is not buildable through the SDK.** `SubprocessCLITransport.connect`
builds `process_env = {**inherited_env, ..., **self._options.env}`. `options.env` *merges over*
`os.environ`; it can set a variable to empty, which is a different state from absent, and it
cannot unset one. And pptmstr is one process running N sessions, so mutating `os.environ` before
spawn is global to every concurrent session and races the pool. There is no per-session
environment scrubbing by any route pptmstr controls. The only mechanism that genuinely unsets is
the CLI's own `sandbox.credentials.envVars` with `"mode": "deny"`.

**The CLI's Bash sandbox is reachable per launch, above user and project settings, without
touching mainline.** `ClaudeAgentOptions.settings` is a `str` passed through verbatim as
`--settings <json>`. It must carry a hand-built JSON string rather than the typed `sandbox` field:
the installed `SandboxSettings` TypedDict declares only `enabled`, `autoAllowBashIfSandboxed`,
`excludedCommands`, `allowUnsandboxedCommands`, `network`, `ignoreViolations`,
`enableWeakerNestedSandbox`, and has no `filesystem`, `credentials` or `failIfUnavailable` key
even though the CLI documents them. (This is a mypy constraint, not a runtime one —
`_build_settings_value` does `json.dumps` with no validation and extra keys survive. It binds here
because this repository runs mypy.)

**It does not sever the gate.** The sandbox wraps Bash commands and their children, not the CLI.
Upstream, verbatim: *"Built-in file tools, MCP servers, and hooks still run directly on your
host."* `PreToolUse` still arrives over the same stdio control channel, `bus.build_server` is an
in-process MCP server that never crosses anything, and `Transcript` is fed from the SDK message
stream rather than the filesystem. Better evidence than that sentence, from `types.py`: the SDK's
own remedy for `bypassPermissions` — which is strictly more aggressive than
`autoAllowBashIfSandboxed` — is *"To gate every tool call, use a PreToolUse hook instead."*
`PreToolUse` is upstream of the permission flow, and `autoAllowBashIfSandboxed`'s documented
effect is entirely inside that flow. **Not settled by reading; see the probe in §8.**

**Separate OS user and container are refused on structure, not cost.** A separate user moves
`$HOME`, so the CLI reads a different `~/.claude`: `brief.default_root()` is
`Path.home()/".claude"/"projects"`, `_seed_brief` writes there as the operator, and
`sessions.enumerate_sessions` plus `tag_session` operate on the operator's store — the resume
picker goes blind to sessions run under the mode. A container preserves the protocol (a custom
`Transport` works, the wire format is line-JSON) but every *path* in the options becomes a
container path with no host meaning: `cwd`, the brief directory, `transcript_path`, the session
store. Both trade away the operator's ability to watch the session, which is the product.

**The upgrade path, which belonged in the original four and dissolves §3's coupling problem.**
`@anthropic-ai/sandbox-runtime` *"wraps an entire process in the same Seatbelt or bubblewrap
isolation… constrains every tool, hook, and MCP server in the session, not only Bash"*, with no
Docker, on the same host, same user, same paths — so the reason separate-user and container were
refused does not apply to it. It is reachable without a custom `Transport`: `cli_path` is spawned
verbatim and `_check_claude_version` swallows its own failure, so a shim script that execs
`npx @anthropic-ai/sandbox-runtime claude "$@"` stands in for the CLI with one string changed.
Against it: a beta research preview whose config format may change, and a Linux deny-list built
once at launch that *"does not cover anything the session creates later"*. **If the policy ever
widens past `Bash`, this becomes the answer rather than an option.**

---

## 5. What survives of the gate design

A `Policy` parameter on `classify` — `classify(tool_name, tool_input, policy=Policy.STRICT)` —
the shape `planning/2026-08-11` §1 already designed, with one preset added. The OFF path is
bit-identical: `classify` has one mainline call site (`driver._gate_tool_use`) plus
`scripts/verify_questions.py`, and every classification test in `tests/test_approval.py` calls
`classify(tool, {})` positionally and passes unchanged. 08-11 names the test that carries this
argument — *"`STRICT` is bit-identical to today — the default-argument path over the existing
corpus."* **That test is not optional; it is the whole of the OFF-path evidence.**

---

## 6. Constraints that still bind

1. **The unknown-tool fallthrough is untouched.** `classify`'s final `REQUIRE_APPROVAL` is pinned
   by `test_unknown_tools_fail_closed`, whose docstring calls it the load-bearing test in the file.
   A policy adds a narrower allowlist beside the existing one; it never changes the fallthrough.
   A mode that is dangerous by choice is still not a mode that admits tools nobody has seen.
2. **`test_nothing_auto_approves_that_can_write` keeps asserting exactly what it asserts today**,
   for `STRICT`. A second test names what the new policy admits. Relaxing the existing test would
   remove the only thing standing between a future contributor and moving those tools into `_AUTO`
   outright.
3. **The dial is displayed.** `planning/2026-08-22` D3 — *"Displaying the dial is part of shipping
   the dial, not a follow-up."* More load-bearing here than at any lower rung.
4. **Sub-agents do not inherit it.** 08-11 §4. One `AgentSession` serves its sub-agents'
   `PreToolUse`, so per-node scoping is required work, not a refinement.
5. **`LaunchSpec.from_record` does not carry the policy**, with the reason as a comment on the
   field. `ui/health.py`'s fork is `actions.fork(LaunchSpec.from_record(root))` — no modal, no
   combo, no confirm. Carrying the policy through it makes an under-gated session inheritable in
   one click from a button pressed for an unrelated reason.
6. **Not persisted in `Settings`.** 08-11 rejected a global toggle: *"the operator sets it for the
   session they are watching and forgets it is set for the four they are not."*
7. **Selecting the mode by template is out.** `app._launch` reads
   `shape = (templates.by_name(spec.template) if spec.template else None) or templates.SOLO`, and
   `LaunchSpec.template` is a name string — so a policy keyed on it, with `shape` silently fallen
   back to `SOLO`, is autonomy-on with containment-off, logged as "launched as solo". And
   `templates.py` is committed to being operator-editable configuration, which is the wrong place
   for a permission.

---

## 7. The pattern worth copying

`driver._gate_tool_use` runs the at-cap deny **before** `classify`, so no policy can widen the
sub-agent cap even by accident. The invariant sits ahead of the policy rather than inside it. Under
this mode that ordering is the only reason the cap still means anything, and it is what the rest of
the ladder should look like.

---

## 8. The containment configuration, and what it requires

```json
{"sandbox": {
  "enabled": true,
  "failIfUnavailable": true,
  "allowUnsandboxedCommands": false,
  "autoAllowBashIfSandboxed": false,
  "network": {"strictAllowlist": true, "allowedDomains": ["api.anthropic.com"]},
  "credentials": {
    "files": [{"path": "~/.ssh", "mode": "deny"},
              {"path": "~/.aws", "mode": "deny"},
              {"path": "~/.claude/.credentials.json", "mode": "deny"},
              {"path": "~/.claude.json", "mode": "deny"}]}}}
```

Four keys are load-bearing and each closes a state in which the operator believes containment is
on and it is not:

1. **`network.strictAllowlist` is the control that matters**, not the credential denials. Those are
   an enumeration control — upstream says plainly there is no built-in deny list, only what you
   name — and they aim at the wrong hazard. Under auto-approved `Bash` the largest blast radius is
   not "steals a token" but "sends the tree somewhere", and `curl -F @file` needs no credential. A
   default-deny egress allowlist catches that and denies `git push` without enumerating where a git
   credential can hide. **If only one thing is built, build this.**
2. **`failIfUnavailable: true`.** On Linux the sandbox needs `bubblewrap` and `socat`; without this
   key a missing dependency makes the CLI warn and run *unsandboxed*. pptmstr never pipes stderr,
   so that warning goes to the terminal pptmstr was launched from and never reaches the ImGui
   window.
3. **`allowUnsandboxedCommands: false`.** The CLI ships an escape hatch: on a violation the model
   may retry with a `dangerouslyDisableSandbox` parameter, which routes through the normal
   permission flow — and under this mode that flow is pptmstr's gate auto-approving `Bash`. Without
   this key the containment is a one-parameter bypass the model can reach and the gate waves
   through.
4. **`autoAllowBashIfSandboxed: false`.** It **defaults to `true`**. Leaving it unset enables a
   CLI-side auto-approve path the design never analysed. It costs nothing to disable, because
   pptmstr's own gate is already auto-approving `Bash` under this mode.

**`credentials.files` deliberately does not deny `~/.claude` wholesale.** `brief.default_root()`
puts every brief under `~/.claude/projects/<slug>/briefs/`; denying the directory blinds the
session to its own premises. The narrow files are the targets. Note also what this buys and does
not: the CLI process is outside the sandbox and is already authenticated, so denying the credential
file stops a `Bash` command reading the token, it does not make the session un-credentialed.

### Three things this configuration does not do for itself

**`failIfUnavailable` cannot protect itself.** On a CLI too old to know the key, the key is inert
and the silent-fallback failure returns one level up. Documented floors — `credentials` 2.1.187,
`filesystem.disabled` 2.1.216, `network.strictAllowlist` 2.1.219 — sit above the SDK's own
`MINIMUM_CLAUDE_CODE_VERSION = "2.0.0"`, and `_check_claude_version` only warns inside
`except Exception: pass`. **pptmstr must read the CLI version at launch and refuse the mode below
a pinned floor**, rather than trusting a key the CLI may not recognise. Whether an unknown settings
key is rejected or silently dropped is not answerable by reading and is part of the probe.

**The clone must be mandatory or this is not containment.** The sandbox's writable region *is*
cwd. Its protected paths cover `.claude/**`, `.mcp.json`, `.git/hooks` and `.git/config` — they do
not cover ordinary project source. `LaunchSpec.cwd` defaults to `"."`, is free text from an ImGui
field, and is not realpath'd on the launch path, so **the default launch makes pptmstr's own
source tree the writable region**: an agent under this mode can rewrite `approval.py` for the next
launch. The boundary would be "the operator typed a different directory", enforced by nothing,
which is exactly the accident-versus-choice distinction this section exists to close. Either
refuse the mode when the resolved cwd is inside the checkout, or make the clone the mode's own
action rather than a field the operator fills in.

**A `denyWrite` on `pptmstr/` is considered and rejected**, because working on pptmstr is most of
what this tool is for. That is a choice, recorded here so it is not mistaken for an oversight.

### The coupling §8 previously got wrong

The containment layer and the policy contents are **not separable**. Upstream is explicit that the
Bash sandbox *"on its own constrains only Bash, so it is not sufficient for fully unattended runs
in either mode."* The rebuttal — that pptmstr still parks everything else at a human, so the
sandbox's coverage and the un-gated set are the same set — holds **only while `Bash` is the only
auto-approved tool**, because `Write`/`Edit`/`MultiEdit` run inside the CLI process, which the
sandbox does not cover. §1's argument was built on auto-approving the writing tools and is
incompatible with this containment.

**Therefore: the under-gated allowlist is `Bash` and nothing else, pinned by a test asserting
exactly that.** Without that test the containment story in this record goes false without anyone
editing the record. Widening past `Bash` requires the whole-process boundary in §4, not this one.

### Remaining open

**Spawns auto-approve under this policy, and the policy inherits to sub-agents. This reverses
`planning/2026-08-22` D2 and the reversal is deliberate.**

D2 reads *"Spawns stay parked at every preset, or the ladder stops below a preset that would
release them"*, on the reasoning that *"a veteran's calibration for 'don't ask' is wrong in the
dangerous direction, because the blast radius is a fan-out rather than a file."* That reasoning is
sound and its **premise has changed**: sandbox configuration is per-CLI-process and sub-agents
share the parent's, so under §8's containment each additional agent has the same bounded reach as
the first. Fan-out still multiplies token spend and write volume. It no longer multiplies reach,
and reach is what D2's argument is about. Nothing else about D2 is disputed — it remains correct
for every preset that is not contained.

Two recorded positions pulled opposite ways here and both survive, because they are about
different quantities. `planning/2026-08-15` says the spawn gate *"is asking a question whose
answer does not constrain anything"* — true of **scope**: approving a spawn says nothing about
what the agent will then select off a board the operator never saw. D2 is about **volume**. The
resolution is that volume already has a better instrument than a gate:

**The sub-agent cap is the volume control, and it is the only bound this policy cannot widen.**
`driver._gate_tool_use` denies at cap on line 1221, *before* `classify` on line 1224, with the
comment *"Ahead of classify, because a spawn that cannot be admitted must not reach"* it. Verified
by reading the ordering this session. That is the pattern §7 already calls the best-shaped thing in
this gate: the invariant sits ahead of the policy rather than inside it. **Set `subagent_cap` low
for this mode.** It is a number the operator sets and can see, which `pool.py`'s docstring gives as
the reason it is a number at all.

**What this costs, stated because it is not obvious:** it costs the measurement's attribution. The
git sensor (§2) attributes by the exactly-one-candidate rule the 09-02 amendment signed, and that
resolves cleanly in a solo session — the root-bracket probe paired 3 of 3. With a fleet, candidates
overlap and the reading degrades to session-level with an ambiguity counter climbing. **The mode
therefore has two configurations that measure different things: run solo to get an attributed
reading, run a fleet to get throughput and a reading that says the session diverged without saying
who.** Choose per run rather than once.

**Two conditions on this decision, and neither is optional.**

1. `planning/2026-08-11` §4 records *"Sub-agents do not inherit it"* as a requirement. Under this
   decision the requirement is **inverted for this policy only**, and that must be an explicit line
   of code rather than a consequence of where the field lives. One `AgentSession` serves its
   sub-agents' `PreToolUse`, so a policy held on the session inherits by default — meaning
   inheritance would otherwise be decided by an implementation detail rather than by this record.
   Whichever way it is built, a test names it.
2. **The cap must actually bound total fan-out, which is not yet established.** `driver.py:1215`
   reads `spawn = tool_name in ("Agent", "Task") and not agent_id`, so the cap check counts only
   root-level spawns; a sub-agent calling `Task` skips it entirely. That is very likely moot
   because sub-agents are not normally given the tool — but this decision makes the cap the single
   load-bearing volume control, and "very likely moot" is not the standard a load-bearing control
   is held to. See §8d.

**Whether the git sensor (§2) is built before the mode.** Build it first — not so the mode produces
a reading from day one, but so the sensor is calibrated against the instrument it replaces *while
that instrument still works*. Built inside the mode, its first reading is taken in the one
condition where nothing can check it. Against: it is ~600 lines over seven files, its OFF path is
**not** bit-identical because it hooks a path that runs at every policy, and gating it behind the
mode to restore bit-identity destroys the STRICT cross-check that is the main argument for it.

## 8a. Build order

1. ~~`scripts/verify_sandbox_gate.py` before any feature.~~ **Written and run, 2026-09-04. The
   design's blocking assumption holds.** See §8c.
2. **`failIfUnavailable` plus a `stderr` callback** wired to the transcript as an error segment,
   plus the CLI version floor check. Until the operator can *see* that the sandbox failed to start,
   no later step means what it claims.
3. **The settings JSON on `LaunchSpec`**, off by default, `from_record` deliberately not carrying
   it — §6.5's reasoning applies unchanged and applies harder to containment than to policy.
4. **`strict_mcp_config=True`** for this mode.
5. **Only then the policy**, `Bash` and nothing else, with the allowlist-contents test.
6. **The mandatory clone**, which contains nothing on its own and is the cheapest item here.

## 8c. The probe result, 2026-09-04

`scripts/verify_sandbox_gate.py`, run against CLI 2.1.251 with `bwrap` and `socat` present. Two
arms over the same three Bash commands — a control with no sandbox settings, and the recommended
configuration with `autoAllowBashIfSandboxed` left at its default `true`, which is the interaction
being measured. The commands were chosen to have unambiguous confined and unconfined outcomes: an
`echo`, a write to `$HOME` (outside cwd, checked on disk by the script rather than taken from the
model's account), and an HTTPS GET to a domain outside the one-entry allowlist.

**Q1 — does `PreToolUse` fire for a sandbox-auto-allowed Bash call? YES.** Three of three, matching
the control arm exactly. The containment does not sever the gate, and the design's blocking
assumption is confirmed by execution rather than inferred from documentation.

**Q2 — is the command actually confined? YES, and the control arm is what makes that meaningful.**
Unconfined, the `$HOME` write landed and the fetch returned `200`. Sandboxed, the write failed with
`touch: cannot touch '/home/wreel/probe-sandbox-escape.txt': Read-only file system` and the fetch
returned exit 56 with an explicit `<sandbox_violations>` block reading
`deny network-outbound example.com:443 (host is not on the allow list)`. Both halves of the
recommended configuration — the writable-region confinement and `network.strictAllowlist` — do what
§8 claims.

**Q3 — does a violation degrade rather than hang? YES.** Violations return to the model as ordinary
tool results carrying the error text, and the session ran to completion.

**Two findings the probe was not designed to look for:**

**A sandbox violation closes its bracket through `PostToolUseFailure`, not `PostToolUse`.** The
first run hooked only `PostToolUse` and saw 1 closing hook against 3 opening ones, which reads
exactly like the permanent-false-RUNNING defect `scripts/verify_post_tool_use.py` exists for. It is
not: re-run with both hooks registered, the two violations arrive as `PostToolUseFailure` and
**zero brackets are left open**. This matters beyond liveness — the git sensor's proposed
window-close sampling trigger (§2) is driven by the closing hook, and under this mode a large
fraction of Bash calls will be violations. **The trigger must observe both closing hooks or it will
stop sampling the moment containment starts doing its job.**

**An unrecognised settings key is accepted silently.** Arm C passed a nonsense top-level key and
the session started normally. So a CLI too old for `strictAllowlist`, `credentials` or
`failIfUnavailable` does not complain — it runs with the key ignored. This confirms the reasoning
in §8: `failIfUnavailable` cannot protect itself, and **pptmstr must read the CLI version at launch
and refuse the mode below a pinned floor.** That is not a belt-and-braces refinement; it is the
only thing standing between a version shortfall and a silently unconfined run.

**What this probe did not establish.** It ran on one host with the sandbox available, so it says
nothing about the `failIfUnavailable` path itself — the loud-failure behaviour when `bwrap` is
missing remains inferred from documentation. It exercised `Bash` only. And it used its own hook,
not pptmstr's `AgentSession._gate_tool_use`; the gate's own behaviour under these settings is
inferred from the hook path being identical.

## 8d. The cap has a hole, and option C is what makes it load-bearing

Established this session by reading, and it is not hypothetical:

- `driver.py:1215` — `spawn = tool_name in ("Agent", "Task") and not agent_id`. The cap check on
  1221 is therefore skipped entirely for a spawn issued *by a sub-agent*.
- `templates.Role.tools` documents `None` as *"inherit everything the session has"*, and
  `Role.tool_list()` returns `None` in that case, which `driver._team()` passes to
  `AgentDefinition(tools=...)`.
- The shipped `feature` template's **`builder` role has `tools=None`**. Every other role in
  `BUILT_IN` restricts to six tools; `builder` does not.

So a `builder` sub-agent can call `Task`, and that spawn is not counted against `subagent_cap`.
Today this is bounded by the gate — the spawn parks and a human sees it. **Option C removes that
bound and promotes the cap to the only volume control, at which point the hole is the whole
question.** The decision above says "set `subagent_cap` low"; a cap that a sub-agent can spawn
around is not a cap.

Three responses, and the choice is not obvious enough to make here:

1. Count sub-agent spawns against the same cap — drop `and not agent_id` from the predicate. The
   comment above it explains the *ordering* but not the exclusion, so the exclusion's reasoning is
   not recorded and may be deliberate for a reason no longer visible.
2. Give the mode's roles explicit `tools` tuples that exclude `Task`, so the fan-out cannot start.
   This is tier-0 capability removal, which `notes/2026-08-31` §4.7 ranks above any gate.
3. Both, on the argument that (2) is per-template data an operator can edit and (1) is structural.

**Not a probe. This one is read-established and needs a decision, not a measurement.** It is
recorded here rather than fixed because option C was chosen minutes ago and the fix belongs with
whoever builds the policy.

## 8e. What the Orca comparison contributes

From `planning/2026-09-03-orca-made-the-opposite-bet-on-the-same-tradeoff.md`, which researched a
convergent competitor independently of this work. Four points bear on the decisions above; the rest
of that record is out of scope here.

**1. Option C is Orca's default, and Orca's own docs recommend against it — for a reason that does
not apply to us.** Orca launches every supported agent with its full-autonomy flag pre-applied
(`--dangerously-skip-permissions`, `--yolo`, `--dangerously-bypass-approvals-and-sandbox`), on the
stated intent that *"the worktree itself is the sandbox."* Its own `agents/supported.mdx` then
carries a callout: *"A worktree is an isolated checkout, not a security sandbox: the agent can still
access files and network resources available to its process."*

That is the same conclusion §4 reached from the other direction — a `git worktree` costs zero code
because it does zero containment — arrived at independently by two research passes and by the
competitor's own documentation. **The bet this record makes is the same as Orca's; the difference
is entirely in the boundary.** Ours is `bwrap` with a default-deny egress allowlist, measured in
§8c to actually stop a `$HOME` write and an off-allowlist fetch. Theirs is a checkout. That
distinction is the whole defence of option C and it should be stated in exactly those terms rather
than as a claim to be more careful.

**2. Under option C the gate is off, so the sensor is the only differentiator left.** That record's
finding is that *"Nobody in this comparison has built what `approval.py` does"* — the gate is the
thing neither Orca nor Claude Agent Teams has. During a dangerously-autonomous run pptmstr
switches that off and is, for the duration, Orca-shaped. What remains distinct is the
declared-versus-actual comparison. **This is the strongest available argument for building the git
sensor (§2) before or alongside the mode rather than after it**, and it is a better argument than
the methodological one already recorded: without it, a dangerous run is a worse Orca.

**3. Option C is the configuration where pptmstr's isolation bet is weakest, and that makes the
units defect urgent.** Orca isolates by construction — one `git worktree` per task, *"what makes
parallel agents safe — they never step on each other's files"* — and has no `touches` equivalent
at all. pptmstr takes the opposite bet: one shared tree, disjointness derived from declared file
overlap in `store._auto_depends`, which its own docstring calls *"the entire mechanism keeping two
agents out of one file, mechanical and unbypassable."*

With the gate on, a human sees every write and the declaration is a second line of defence. Under
option C with a fleet, `_auto_depends` is the **only** thing keeping two agents out of one file —
and `scripts/verify_declaration_units.py` demonstrated this session that it silently misses the
collision between `pptmstr/store.py` and `store.py` when a session's cwd is not the repository
root. The control case collides; the mismatched-units pair does not. **The units defect is
therefore a precondition of option C, not an unrelated bug**, and the mandatory-clone requirement
in §8 interacts with it: a clone whose root is not the session's cwd reintroduces exactly the
mismatch.

**4. One cheap adoption, unrelated to the rest.** Orca treats *"a non-empty custom value as an
explicit override and opts that agent out of future permission-mode migrations."* An operator who
hand-edits the sandbox settings JSON should keep that value when pptmstr later changes its own
defaults. Costs nothing to adopt as a rule now, and prevents a future default from silently
widening a boundary an operator narrowed on purpose.

**Also carried forward, and it is a caution rather than a contribution.** That record warns that
`notes/2026-08-31` is *"the most quotable and most misciteable file in the repository"* — untracked,
self-declaredly undecided, and written by an author who lists `approval.py`, `store.py`,
`driver.py` and `bus.py` among the files they did not open — and asks that its thesis be argued
against the code before it becomes a direction. This session did that: reading those four files is
what produced §1's finding that the mode unsolders the instrument. The caution was met, and the
note's thesis survives contact with the code in a narrowed form.

## 8b. What remains uncontained

The operator is choosing to run something dangerous and is entitled to an accurate list of the
edges rather than a short one.

1. **Everything the CLI process itself does.** `Read`, `Edit`, `Write`, `WebFetch`, `Glob`, `Grep`
   run in the CLI, not the sandbox. They are gated today; containment does not follow them if that
   changes.
2. **The working tree, entirely** — including pptmstr's own source under the default cwd. See
   above; the clone is the answer and must be mandatory.
3. **Untracked files inside cwd.** `rm` of an untracked file inside the writable region has no
   recovery path. `CLAUDE.md`'s `git add` rule stops being hygiene under this mode.
4. **Exfiltration through any allowed domain.** The proxy allows on the client-supplied hostname
   and by default does not terminate TLS, so domain fronting is available. Allowing `github.com`
   would be allowing an exfiltration channel; the allowlist has one entry for that reason.
5. **Anything in `excludedCommands`.** Upstream suggests excluding git because `git merge` and
   `git checkout` can fail under the sandbox. Excluding git removes containment for git, including
   `git push`. Take the failure instead.
6. **Per-role containment does not exist.** Sandbox config is per-CLI-process and sub-agents share
   the parent's, so every role in a team gets the same boundary — including roles whose
   `Role.tools` never mentions `Bash`.
7. **Nested-sandbox weakness** if pptmstr is ever itself run inside a container
   (`enableWeakerNestedSandbox`).
8. **What is sent to the API.** Isolation changes nothing about what leaves the machine for the
   model. Anything the agent reads is transmitted, sandboxed or not.

---

## 9. Deferred, explicitly — do not pick these up

- Content-classifying `Bash` in the gate. §3; both routes are closed.
- cwd-containment in the gate. Still the separate decision `planning/2026-08-11` declined to make,
  and §4 supersedes the need for it under this mode.
- Denying on divergence. Unchanged from `planning/2026-09-01`; this record does not touch it.
- Persisting the policy anywhere.

---

## 10. Verification standard for this record

§4 and §8's mechanical claims about the SDK were established by two agents independently reading
the **installed package source** at `.venv/lib/python3.11/site-packages/claude_agent_sdk/` —
`types.py` and `_internal/transport/subprocess_cli.py` — not from recalled knowledge and not only
from documentation, per `CLAUDE.md`. The second re-read every mechanical claim of the first and
confirmed each: the `options.env` merge, `_build_settings_value` returning `None` when both fields
are unset (which is the whole OFF-path argument), the `SandboxSettings` key set, the
`AgentSession._options` field list, and the `LaunchSpec.cwd` default. Both upstream quotations were
confirmed verbatim against `code.claude.com/docs/en/sandbox-environments`.

**Three claims in §4 and §8 are not settled by reading and are the reason §8a.1 is first:**
whether `PreToolUse` fires for a sandbox-auto-allowed `Bash` call; whether an unrecognised settings
key is rejected or silently dropped; and the installed CLI's version against the documented floors.
The evidence for the first is strong and indirect — the SDK's own remedy for the more aggressive
`bypassPermissions` is a `PreToolUse` hook — but no source states the sandbox case, and a design
that rests on an inference should say which inference.

§1's claims about the gate and store paths were verified by execution this session: `grep` over
`pptmstr/driver.py` and `pptmstr/store.py` for the construction sites and the sole writer,
`grep -A14 "^def written_path" pptmstr/model.py` for the `Bash` blind spot, and
`python -c "import pptmstr.store"` for the tree's state. Everything else is read-derived from the
files cited.

**Nothing here has been run as a feature, and the suite was not run for this record** — the
working tree carries the operator's in-flight `model.py`/`store.py`/`test_store.py` changes, so a
gate reading taken now would not separate this record's writes (two markdown files, which cannot
affect it) from that work.

§1 is falsifiable in one run: launch under the proposed mode and read
`Snapshot.unattributed_writes` and any claimed task's `writes`. They should be empty. If they are
not, §1 is wrong and this record's central argument fails with it.
