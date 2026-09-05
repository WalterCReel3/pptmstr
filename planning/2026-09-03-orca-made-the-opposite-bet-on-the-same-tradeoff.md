# Orca made the opposite bet on the same tradeoff

**Dated:** 2026-09-03 · **Status:** research complete, nothing built — five candidates,
one recommended · **Found by:** an operator asking how this project compares to
[onorca.dev](https://www.onorca.dev/) · **Companion:**
[`2026-08-11-agent-teams-vs-pptmstr.md`](archive/2026-08-11-agent-teams-vs-pptmstr.md),
which reached the same convergence finding about a different competitor and is not
restated here.

Symbol names, not line numbers, per the 08-14 convention.

Four agents ran this: two investigators (Orca from published sources; pptmstr from the
tree), then two skeptics (one attacking the comparison as a whole, one independently
re-retrieving the Orca quotes). **Nothing was run.** No build, no tests, no launch of
either tool. Every claim below is read-derived, and the performance figures this
repository states about itself are relayed as its claims, not confirmed as facts.

---

## Read this first: the retrieval channel is compromised, and it is not a small problem

`WebFetch` does not return raw HTML. Every string reaches the caller through a
summarizing model that caps quotes at ~125 characters and paraphrases past that. That
was known going in. What was **not** known, and what the skeptic demonstrated twice:

- Asked for a character-for-character quote of a raw `.md` file, it returned a paragraph
  about only taking "irreversible or externally visible actions that have been
  explicitly pre-authorized for that task" **as page content**. That text was from the
  requesting agent's own system prompt. Challenged, it conceded the strings appeared "in
  the organization instructions section, not in the web page content itself".
- On `automations.mdx` it asserted the file "explicitly notes they operate 'unattended'"
  while the quote it supplied did not contain the word.

So the relay does not only truncate and paraphrase — **it can present the caller's
surrounding context as source text.** Any future research task that rests on a fetched
quote inherits this.

**The mitigation that held, and should be the house rule:** fetch
`raw.githubusercontent.com/<org>/<repo>/main/<path>` — a payload where markdown
conversion is a no-op — and end the prompt with "Report only what is in this file." Ask
for short exact quotes; "reproduce verbatim" trips a copyright guard and fails. For
absence claims, ask positively ("list every heading and every `--flag` in this file")
rather than yes/no, because absence is the mode where the injection failure hides.

One thing that was *not* a relay failure, corrected during the research: the first pass
reported the summarizer wrongly answering "No evidence of prompt bypass" for
`/docs/agents/claude-code`. Read from raw source, that page genuinely contains no
bypass, permission, or yolo text. **The summarizer was right there**, and that example
should not be used to discount the first pass's other retrievals.

---

## What Orca is

`github.com/stablyai/orca`. MIT (confirmed three ways: GitHub API `spdx_id`, root
`LICENSE`, README badge). Stably AI — YC **Winter 2022** per YC's own company page, four
people, San Francisco, founders Jinjing Liang and Neil Parker. One third-party article
slug says "yc-w2026"; unreconciled, take YC's own page.

Desktop for macOS/Windows/Linux, plus iOS/Android companions and a CLI. It drives agent
CLIs you already have by shelling out to them, so it never handles a key or a token —
`agents/supported.mdx` names **35** (the homepage says 27, the repo says 30+, one pass
counted 40+; the number is a moving target, do not quote one). It self-describes as an
**ADE — Agent Development Environment**. Three different taglines across README, site,
and GitHub description; anyone quoting "the tagline" must say which.

`v1.4.196`, published 2026-09-03T01:50:54Z, near-daily cadence, ~2.5k open issues.
`package.json` on `main` reads `1.4.178-rc.2` — **the branch version lags the tag, do not
cite `package.json` as current**. Repo `created_at` 2026-03-17; 60,843 stars as of the
2026-09-03 fetch. That trajectory is unusual enough that no argument here rests on it,
and the figure goes stale in days.

**It is not purely local, and it is not purely open.** Two claims got collapsed during
this research and the correction matters:

- `ways-to-run.mdx` documents **four** execution modes: local ("Agents, terminals, and
  the browser run on the same machine as the UI"), SSH ("Agents and `git worktree` run on
  the remote; the editor, diff, and UI stay on your laptop"), remote Orca servers ("The
  **server** owns projects, worktrees, terminals, and agent processes; clients are the
  UI"), and per-worktree cloud VMs or local Docker. Corroborated structurally by
  `docs/reference/ssh-execution-boundary.md`, `headless-linux-server.md`,
  `relay-regional-placement.md`, `remote-wire-compatibility.md`,
  `ssh-host-key-verification.md`.
- "Orca does **not** sell managed VPS hosting. Remote modes always use machines and cloud
  accounts you control" is separately true and is a **different claim**. Remote execution
  is shipped; managed hosting is not sold.
- `cloud/README.md` describes a mobile relay on Google Compute Engine cells with Cloud
  SQL and references *"the private `stablyai/orca-cloud` repository"*. "No account
  system, fully open" is true of the desktop app and overstated for the product.
- "No user account information (Orca has no account system)" is real but is a
  parenthetical inside the list of things telemetry never transmits, not a standalone
  product claim. Quote it with that context.
- Telemetry is **opt-out, on by default**, to PostHog Cloud US. `telemetry.mdx` has a
  "How to opt out" heading and three disable routes; the phrase "opt in" appears nowhere.

---

## The convergence is real, and this project conceded it first

`cli/orchestration.mdx`, opening line, confirmed verbatim by two agents from raw source
under independently worded prompts:

> Orchestration is Orca's structured multi-agent layer: a **Run** (namespace + coordinator
> inbox), **Tasks**, **Dispatches**, supervised **workers**, messages, and decision gates.

Its own definitions: Run — "durable namespace and home inbox. Never schedules or places
workers."; Task — "a work item with a spec, dependencies, and status: `pending`, `ready`,
`dispatched`, `completed`, `failed`, or `blocked`."; Dispatch — "one attempt of a task on
a terminal; lifecycle authority for `worker_done` / heartbeat."; Message — "inbox mail
(`status`, `dispatch`, `worker_done`, `escalation`, `question`, `heartbeat`, …).";
Decision gate — "a coordinator-owned question that blocks a task until it is resolved."
The phrase "task DAG" is on the page.

**Do not call our board a novel coordination primitive.** The 08-11 record already
settled this against agent teams — "Six of seven rows are the same product. That
similarity is the finding, not a coincidence" — and the same holds here. Two independent
convergent implementations is evidence the decomposition is right.

It is not our board with different nouns. Two structural differences, verified on both
sides:

| | Orca | pptmstr |
|---|---|---|
| Placement | **push** — `orca orchestration dispatch --task <id> --to <workerHandle>`; no worker-side claim of an unassigned task exists on that page | **pull** — `bus.claim_task` with no id returns the oldest task whose dependencies are met; work-stealing, not scheduling |
| Isolation | one `git worktree` per task — "This is what makes parallel agents safe — they never step on each other's files" | one shared tree; dependencies derived from declared file overlap in `store._auto_depends` |
| Blocked | stored as a task status | derived — `model.TaskState` is "Deliberately without a BLOCKED member"; `Task.blocked_on` computes it |
| Messages | six types (`status`, `dispatch`, `worker_done`, `escalation`, `question`, `heartbeat`) | one untyped `Concern` with an optional `task_id` |

That last row is a place we are **thinner**, not richer. Say so.

**There is no `touches` equivalent in Orca.** Firmed up from weak absence to positive
finding: "conflict", "lock", "owner", "ownership", "same file" appear in none of
`model/worktrees.mdx`, `cli/orchestration.mdx`, `cli/reference.mdx`,
`skill-guides/orchestration.md`, or the flag list in `src/cli/specs/orchestration.ts`.
`orca orchestration send --files-modified "path/a,path/b"` exists, but it is a **report**
inside a `worker_done` payload with nothing documented consuming it for arbitration.
`--deps <json_array>` is task-level only.

---

## The split: where the human stands

`model/agents-sessions.mdx`, "Launch defaults", confirmed verbatim on raw source in two
files and on the deployed site:

> Orca launches every supported agent with its full-autonomy permission flag pre-applied
> — Claude with `--dangerously-skip-permissions`, Codex with
> `--dangerously-bypass-approvals-and-sandbox`, Gemini with `--yolo`, and the equivalent
> for each other agent in the picker. The intent is that the worktree itself is the
> sandbox: agents can do their thing without per-tool approval prompts breaking the flow.

**Two corrections that any future write-up must carry, or it will be attacking a straw
man.**

First: `agents/supported.mdx` carries a callout titled "Permission safety" — *"A worktree
is an isolated checkout, not a security sandbox: the agent can still access files and
network resources available to its process. Choose **Manual** in **Settings → Agents →
Agent Permissions** unless you intentionally trust the agent and the task."* **Orca's docs
recommend against Orca's default, and have already made the criticism against
themselves.** Draw the contrast on the *default*, not on the rationale. (Confirmed in repo
`main`; a live-page fetch did not surface this callout, and deployed lag could not be
distinguished from a summarizer miss.)

Second: it is **opt-out, not locked** — a global Yolo/Manual switch, a per-agent
launch-argument override with a Reset button, and "Orca treats a non-empty custom value
as an explicit override and opts that agent out of future permission-mode migrations."
Accurate framing is *default-on with a documented global off switch*.

**The sharper contrast, and the one worth keeping.** `settings.mdx` defines Manual as
*"keep each agent's own approval flow."* Orca in Manual reviews nothing itself — it stands
aside and lets each CLI's own TUI prompt fire. So **Orca has a permission switch; pptmstr
has a permission mechanism and no switch** (`settings.Settings` carries no approval
field; `2026-08-22-session-controls-and-the-mode-dial.md` is "proposals, none built").

**Orca's decision gates are not a human-in-the-loop story.** This was attacked
deliberately, because a real approval story there would have collapsed the comparison.
Settled on positive evidence, not absence, across five locations: `skills/orchestration/
SKILL.md` is an agent-facing skill file; `skill-guides/orchestration.md` addresses the
coordinator and says "Use `gate-create` only for coordinator-managed task DAG decisions";
`cli/orchestration.mdx` says "Use explicit gates when the coordinator has created a task
DAG and wants to block a task until a decision is recorded" and "Use `orca orchestration
ask` for blocking questions **instead of local TUI prompts**"; `src/cli/specs/
orchestration.ts` help strings name no actor; and the decisive one, the handler test
`src/cli/handlers/orchestration-gate-cli.test.ts`, whose names are *"sends the bound
coordinator handle to gateCreate"* and *"scopes gate-list to the caller when no Run is
named"*. **Gates are authorised against a coordinator handle bound to a terminal.** A
human can type the command; there is no operator-facing gate surface.

The synthesis that follows from it: Orca's one human-attention surface — the Agent
Dashboard "Needs You" column — is fed by terminal permission prompts, which the default
suppresses.

Orca's gates and its `escalation`/`question` messages are our `post_concern` /
`read_inbox`. They are not our approval gate. **Nobody in this comparison has built what
`approval.py` does.**

---

## Corrections made during the research — carry these forward

Four claims were stated confidently and turned out to be wrong. Three were the
coordinator's.

1. **"pptmstr gates every tool call before it executes" — false.** `approval.classify`
   auto-approves `_AUTO` (`Read`, `Glob`, `Grep`, `NotebookRead`, `TodoWrite`,
   `ListMcpResources`, `ReadMcpResource`) and `_BUS_AUTO` (five of seven bus tools;
   only `post_concern` and `declare_task` are reviewed). **Use the README's own line:
   "Every mutating call is shown before it runs."** The strong true property is that
   classification is fail-closed, so an unrecognised tool requires approval.
2. **"Both are local desktop software" — false**, see the four execution modes above.
   This originated as an investigator's warning and was inherited by the coordinator
   without checking. The row it would have deleted is a real one: remote execution is
   shipped by Orca and is a recorded pptmstr non-goal.
3. **"`planning/` holds work not yet started; `planning/archive/` holds work that
   landed" — false in both directions**, and it had been written into a worker's task
   spec as though established. `2026-08-31-sessions-can-be-resumed-and-bookmarked.md`
   says "built and green" and is not in the archive; `archive/2026-08-09-dogfooding.md`
   says "open" and is. A `Status:` line can itself be stale — *this file's own §10 is
   cited below as a live recommendation while its status line reads "open, no code
   changed"*, and `bus._board_line` implements several of its items today. **The only
   authority for "is it built" is the source tree, module and symbol. Neither folder is
   evidence.**
4. **`notes/2026-08-31-the-instrument-is-the-product.md` is not this project's thesis.**
   It is untracked in git, says of itself "nothing here is decided", and its author lists
   `approval.py`, `store.py`, `driver.py` and `bus.py` among the files they did not open.
   It is the most quotable and most misciteable file in the repository.

---

## What to steal, ranked

### 1. A worktree for the gate — not for the agents

`2026-08-15-what-the-board-does-not-carry.md` §10 already names this: *"A structural
answer already exists and is not being used: run the gate on a tree nobody is writing. A
git worktree, or any snapshot copy, gives the gate a stable read at the cost of a
checkout."* The same section records concurrent agents producing test failures nobody
could attribute.

Take it for the gate run only. Copying Orca wholesale — a worktree per task — would
undercut `touches`, which exists *because* agents share a tree; that trades a declaration
for filesystem separation and pays for a mechanism whose problem it just deleted. The
narrow version buys the thing we lack: a `make check` result taken against a tree that is
not mid-save.

This is the one place Orca's design solves a problem our own records document us having.
Cost: a checkout per gate run, and a story for uncommitted work.

### 2. Typed messages

Six types against our one untyped `Concern`. Cheap, and it is the smell `STYLE.md`
already names — an error message that does not distinguish the two mistakes it covers.

The payoff is not tidiness, it is `Snapshot.needs_you`. The README lists "needs_you sorts
two different clocks" as open. A worker blocked on an answer and a worker reporting an
observation are different obligations with different urgency, and the projection cannot
currently tell them apart because the record does not carry the distinction. Add the type
and the sort becomes derivable rather than heuristic — which is the house rule anyway.

### 3. `--files-modified`, done properly — the recommended one

Orca collects a modified-file list in its `worker_done` payload and nothing consumes it.
We have the inverse: `touches` is a **declaration** made at `declare_task`, and the gate
already sees every mutating call, so the **actual** write set passes under our nose for
free. Nobody diffs them. A worker that declared `store.py` and wrote `driver.py` is
currently indistinguishable from one that did what it said.

Neither project can do this today: Orca has the data and no gate; we have the gate and do
not compare. It is also the cheapest to build, because both halves already exist.

**Caveat that must travel with it:** the untracked note argues exactly this — that the
thesis should move from the gate to declared-vs-actual instrumentation ("The gate is not
the product. The gate is the sensor"). This research converges with a live proposal
rather than settling it, and the note's author had not read `approval.py`, `store.py`,
`driver.py` or `bus.py`. Make the argument against the code before this becomes a
direction.

### 4. One design detail from the permission switch — not the switch

Do not take the switch. The whole position is a mechanism with no dial, and Orca's own
docs telling users to leave the default is not an advertisement.

Take the small thing: *"a non-empty custom value is an explicit override and opts that
agent out of future permission-mode migrations."* An operator customisation that survives
the tool changing its own defaults underneath it. Costs nothing to adopt as a rule for
whatever lands in `settings.py` later.

The scoped version of the dial is already recorded as worth building — a narrowly scoped
run capability for review roles, so a reviewer stops being structurally incapable of the
"works end to end" claim (`templates.py` records that conflation).

### 5. A fetchable skill doc — as evidence in an argument we have open

Orca exposes its coordination primitive to agents as `orca skills get orchestration
--full`: a document workers pull, not prompt text they are launched with.

`templates.py` says "The prompts are the feature."
`2026-08-15-a-task-reaches-the-board-without-a-decision.md` says "Prose is the weakest
mechanism available." Both are in the tree, unreconciled, and the untracked note calls
resolving that the most useful thing in the repository. Orca resolved it toward
pull-on-demand. Whether they are right is a separate question — but it is an outside data
point in the one argument flagged as deciding what this tool is.

---

## What not to take

Autonomy-by-default and scheduled unattended automations (`cli/automations.mdx`:
`--trigger` takes `hourly`, `daily`, `weekdays`, `weekly`, cron, or RRULE) are the bet
this project deliberately did not make. Mobile and remote execution: remote is a recorded
non-goal (`README.md`, "Non-goals: i18n, multi-user, remote access") — though recorded
**without an argument**, unlike our other refusals, which is worth knowing if it is ever
revisited.

Multi-provider is a **genuine absence with no recorded refusal** — grepping every `.md`
for provider/Codex/Gemini/OpenAI and `orchestrator-design.md` for "provider" found
nothing. Do not present it as a decision. *Inference, labelled as such:* it is not free
for us the way it is for Orca, because they shell out to CLIs while our parking invariant
is an in-process `PreToolUse` hook via `ClaudeAgentOptions.hooks` — so a provider seam
costs the mechanism. The tree makes that argument about teammates, not about providers.

---

## The detail worth the whole exercise

`agents/supported.mdx` lists **Claude Agent Teams** as a supported agent — `orca
claude-teams`, native panes per teammate, *disabled by default*. That is precisely what
[`2026-08-11-agent-teams-vs-pptmstr.md`](archive/2026-08-11-agent-teams-vs-pptmstr.md)
evaluated and refused, on the grounds that teammates run outside the driver and inherit
permission mode wholesale, so adopting it would **remove** the parking invariant — "a
fork rather than a deferred feature."

Orca adopted it. It can afford to, because it is not holding a gate that adoption would
break. Two projects, the same feature, opposite answers, each consistent with its own
bet.

---

## Open, and what nobody verified

- **Nothing was run, on either side.** Every measured figure this repo states about
  itself — idle CPU 2.0% parked vs 13.3%, the hook timeout aborting at exactly 2.0s,
  `initialPrompt` not reaching a worker — is *the repository's claim*, sourced to a
  script whose output no agent in this session saw. Label them that way or run them.
- `orchestrator-design.md` was navigated by heading (§0, §1, §5.2.1, §8, §9), not read
  end to end. A non-goal recorded only in §2.x, §6 or §7 was not seen.
- Only the permission-default claim was confirmed against both repo `main` and the live
  site. Everything else is `main` only. `docs/site` is a Next/Fumadocs build over
  `docs/site/content/docs` per `vercel.json` and `source.config.ts`, so `main` is the
  upstream — but deployed lag is unmeasured.
- Whether Orca persists full conversation transcripts or only terminal scrollback and
  working directory. `model/agents-sessions.mdx` deferred it to a page nobody retrieved.
- `gate-list` exists in the skill guide and is absent from the docs page's command list;
  the page itself warns "Command flags evolve with the app." **Treat the docs page as
  lagging the skill guide.**
- The `onorca.dev/enterprise` page exists (nav: Docs, Changelog, Enterprise, Download)
  but appears not to be a paid tier — footer says "Free and open source", CTA is "Get in
  touch", no SSO or login mentioned. What Enterprise adds as software is not determinable
  from published material.
- Prose/code disagreements found in *this* tree and left unfixed: design §9 still lists
  subagent-spawn gating as an open decision though `approval._REVIEW` settled it;
  `pyproject.toml`'s ruff comment claims `make lint` runs `ruff format --check` when the
  Makefile runs `ruff check` + `black --check`.

---

## Sources

**Orca, read from raw MDX** at
`raw.githubusercontent.com/stablyai/orca/main/docs/site/content/docs/`:
`ways-to-run.mdx` · `agents/supported.mdx` · `agents/claude-code.mdx` ·
`model/agents-sessions.mdx` · `model/worktrees.mdx` · `cli/orchestration.mdx` ·
`cli/automations.mdx` · `cli/reference.mdx` · `settings.mdx` · `telemetry.mdx` ·
`activity.mdx`. Also `skills/orchestration/SKILL.md`, `skill-guides/orchestration.md`,
`src/cli/specs/orchestration.ts`, `src/cli/handlers/orchestration-gate-cli.test.ts`,
`cloud/README.md`, `docs/reference/*`.

**Orca, via the site and other surfaces** (subject to the relay caveat above):
[onorca.dev](https://www.onorca.dev/) · [/docs](https://www.onorca.dev/docs) ·
[/docs/cli/orchestration](https://www.onorca.dev/docs/cli/orchestration) ·
[/docs/model/agents-sessions](https://www.onorca.dev/docs/model/agents-sessions) ·
[/docs/agents/supported](https://www.onorca.dev/docs/agents/supported) ·
[/docs/review/annotate-ai-diff](https://www.onorca.dev/docs/review/annotate-ai-diff) ·
[/docs/notifications](https://www.onorca.dev/docs/notifications) ·
[/docs/ways-to-run](https://www.onorca.dev/docs/ways-to-run) ·
[/docs/telemetry](https://www.onorca.dev/docs/telemetry) ·
[/changelog](https://www.onorca.dev/changelog) ·
[/enterprise](https://www.onorca.dev/enterprise) ·
[github.com/stablyai/orca](https://github.com/stablyai/orca) ·
[releases](https://github.com/stablyai/orca/releases) ·
[YC: Stably AI (Orca)](https://www.ycombinator.com/companies/stably-ai-orca) ·
[Product Hunt](https://www.producthunt.com/products/orca-5) (launch ~April 2026, 89
upvotes, derived from a relative "5 months ago" — approximate).

**pptmstr, by symbol:** `approval.classify` / `_AUTO` / `_BUS_AUTO` / `_REVIEW` /
`_BUS_POST` · `bus.build_server` / `BUS_TOOLS` / `_board_line` / `_sender` /
`UnstampedCall` / `_premises_text` · `driver.AgentSession._gate_tool_use` /
`_stamp_bus_call` / `_park` · `model.TaskState` / `Task.blocked_on` / `Task.is_claimable`
/ `normalised_touches` / `TaskRefusal` · `store._auto_depends` / `_pick_claim` / `_apply`
· `settings.Settings` · `templates.BUILT_IN` / `READ_ONLY_TOOLS` / `worker_prompt` /
`lead_briefing` · `board.board_tasks` / `role_name` / `_addresses` · `pool.SessionPool` ·
`brief.py` · `ui/review.py` `SHORTCUTS` / `handle_keys` / `approve_everything`.

**pptmstr, by record:** `README.md` · `STYLE.md` · `orchestrator-design.md` §0, §1,
§5.2.1, §8, §9 · [`archive/2026-08-11-agent-teams-vs-pptmstr.md`](archive/2026-08-11-agent-teams-vs-pptmstr.md)
· [`2026-08-15-what-the-board-does-not-carry.md`](2026-08-15-what-the-board-does-not-carry.md) §10
· [`2026-08-22-session-controls-and-the-mode-dial.md`](2026-08-22-session-controls-and-the-mode-dial.md)
· [`2026-08-15-a-task-reaches-the-board-without-a-decision.md`](2026-08-15-a-task-reaches-the-board-without-a-decision.md)
· [`archive/2026-08-17-a-session-premise-is-a-place-not-a-message.md`](archive/2026-08-17-a-session-premise-is-a-place-not-a-message.md)
· `notes/2026-08-31-the-instrument-is-the-product.md` (untracked, undecided).