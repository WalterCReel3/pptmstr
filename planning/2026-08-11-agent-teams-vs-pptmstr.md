# Agent teams solved the coordination half; the control half is still ours

**Dated:** 2026-08-11 · **Status:** decided — not adopted, primitives stolen;
**folded into the spec as rev. 4** · **Follows:** the §0 "What you are not building" list

Claude Code shipped **agent teams**: a lead session that spawns named teammates, a
shared task list they claim work from, and a mailbox they message each other
through. Read plainly, that is the team-lead / feature-worker / QA / build-specialist
template this project wanted to build, already built, and given away.

It is not a reason to stop. It is a reason to be precise about which half of the
problem this project is actually solving, because the overlap is real and the
divergence is structural rather than cosmetic.

Everything below was read from the docs against the **bundled CLI, 2.1.226**
(`claude_agent_sdk/_bundled/claude`), which clears every version gate mentioned —
2.1.178 (teams as documented), 2.1.198 (background subagents by default), 2.1.199
(name-collision check), 2.1.206 (sibling roster), 2.1.224 (cross-session messaging).
None of this is aspirational; it is all present in what we ship today.

---

## What overlaps

| | Agent teams | pptmstr |
|---|---|---|
| Many agents at once | teammates, own context each | `SessionPool`, `--cap` |
| Roles | subagent definitions reused as teammate types | `AgentDefinition` per work template |
| See them all | agent panel below the prompt, or tmux/iTerm2 panes | tree pane, inbox rail |
| Open one and steer it | `Enter` on a panel row | `TALK`/`TRANSCRIPT` panes |
| Interrupt one | `Escape` on a panel row | `SessionPool.interrupt()` (unwired — dogfooding) |
| Work backlog | shared task list, dependencies, self-claim | `review_queue` is the analogous projection |
| Agent → agent | mailbox + `SendMessage` | nothing yet |

Six of seven rows are the same product. That similarity is the finding, not a
coincidence: it is convergent evidence that lead + named workers + a claimable
backlog + a message channel is the right decomposition. Treat their version as a
reference implementation of the parts we had not built yet.

## What does not overlap, and why it is structural

**Agent teams has no parking invariant** (I8, §1: parking is unbounded and free — the
gate can block indefinitely, and blocking costs nothing). Not weakly —
architecturally:

- Teammates start with the lead's permission settings, and per-teammate modes
  **cannot be set at spawn**. If the lead runs `--dangerously-skip-permissions`,
  every teammate does.
- Teammate permission prompts surface in the lead session as ordinary prompts.
  Approve or deny. No diff, no queue, no wait-time ordering, and no
  edit-the-arguments-then-approve — the interaction this project exists for.
- Plan approval is the one structured gate, and it is explicitly *not* ours: "the
  lead makes approval decisions autonomously," without prompting the human.

So goal #2 is not something agent teams does badly. It is not attempted.

**And adopting it would remove the gate we have.** Teammates are separate Claude
Code processes, not children of our driver. Our gate is an in-process `PreToolUse`
callback registered through `ClaudeAgentOptions.hooks` (`driver.py`); a teammate
process loads *settings-file* hooks
instead. A teammate's `Edit` has no path through `AgentSession`'s
`asyncio.Future`, which is the whole of the parking invariant.

That makes this a fork rather than a deferred feature. Either agents run under our
driver and the parking invariant holds, or they run as teammates and it does not. There is no
incremental adoption where we get the task list now and the gate back later.

**Two more gaps, smaller but real:**

- `TeammateIdle`, `TaskCreated`, and `TaskCompleted` are **TypeScript-only** hooks.
  A Python host cannot observe team lifecycle events even from outside.
  `SubagentStart`/`SubagentStop` — the ones we already consume — are in both.
- One team per session, lead fixed for the session's lifetime, no nested teams, and
  in-process teammates are not restored by `/resume`. Our pool is N independent
  roots across projects; agent teams is scoped to one session's working directory
  and has no cross-project story at all.

---

## What we steal

Every item here is available **without** `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`,
runs inside sessions we own, and stays under the gate.

### 1. `SendMessage` between our own agents

The tool is not team-gated. Only the structured team-protocol messages
(`shutdown_request`, `plan_approval_response`) require teams; plain agent-to-agent
messaging works in any session. Three behaviours to design around:

- A subagent whose tool list includes `SendMessage` starts with a **sibling
  roster** — a system reminder listing `main` and every other *named* agent, each a
  valid `to` value. It appears only when at least one other agent has a name, and it
  is a **snapshot taken at spawn**, so agents named later are invisible to it. Spawn
  order is therefore load-bearing: workers that must talk to each other have to
  exist before the ones that address them.
- **A completed subagent that receives a `SendMessage` auto-resumes in the
  background**, with no new `Agent` invocation. This is what turns a finished worker
  into a teammate you can re-engage — and it means a node can leave a terminal-looking
  state without an intent from us. The store must model it.
- A subagent stopped via the SDK's `stop_task` does *not* auto-resume; the send is
  refused. `TaskStop` from the model does resume. Our stop semantics (§9's
  three-way interrupt/disconnect/kill question) now has a fourth axis: whether
  the thing we stopped can be woken by a sibling.

Background subagents — the default since 2.1.198 — keep `SendMessage` and lose
`ListAgents`.

### 2. Task-list semantics

Copy the model, not the files: dependency edges between tasks, a pending task with
unresolved dependencies being unclaimable, automatic unblocking on completion, and
**file-locked claiming** so two workers cannot take the same item. That last one is
the detail worth having been told rather than rediscovered. This lands next to
`review_queue` in `store.py:323` as a second cross-agent projection.

### 3. On-disk shapes worth reading

- Mailbox: `~/.claude/teams/{team}/inboxes/{agent}.json`, one file per agent.
  Malformed entries are dropped with an error and the valid ones still deliver — a
  robustness stance worth matching in our own envelope.
- Team config: `~/.claude/teams/{team}/config.json`, a `members` array of name +
  agent ID, lead carried as agent type `team-lead`.
- Team name is `session-` + the first eight characters of the session ID.

### 4. The team-shaped prompts

The lead's job description — break work into tasks, assign or let workers claim,
wait for teammates rather than implementing yourself, synthesise at the end — is
prompt content we need for the same roles, and theirs is tested. Same for the
adversarial-investigation pattern (workers explicitly trying to disprove each
other), which is a better fit for the research-coordinator template than
fan-out-and-summarise.

### 5. Cross-session messaging, for the pool

Separately from teams: sessions can message each other by name (`--name` / `/rename`)
over a per-session Unix socket, never through Anthropic servers when both are local.
The path is exported to hooks and Bash as `CLAUDE_CODE_MESSAGING_SOCKET` — which is
the **only documented host → session injection path**, since there is no SDK API to
send a message to a specific agent. Constraints: plain text only, macOS/Linux,
first-party API only (absent on Bedrock/Vertex/Foundry), inbound gating via
`crossSessionInbound`, 50-message queue cap, repeated-message throttling.

This, not agent teams, is the primitive that matches "across projects and domains".

---

## What we keep owning

The routing is a means; the product is that a message is reviewable. Concerns should
be first-class store objects flowing through the machinery already built — a QA
worker's concern arriving at a feature worker ought to be inspectable, and editable
in flight, exactly like a diff. That argues for an **in-process MCP server**
(`create_sdk_mcp_server` + `@tool`) exposing `post_concern` / `read_inbox` /
`claim_task`, backed by our store, rather than leaning on `SendMessage` as the
transport for everything.

`SendMessage` still earns a place for the cases where the model should route without
us in the loop. Both can coexist; the MCP bus is the default and the one that renders.

> **Correction, 2026-08-12.** The paragraph above understates what the MCP bus costs
> and overstates what `SendMessage` buys. An in-process MCP handler is handed only a
> tool name and its arguments — no session, no agent, no tool-use id — so the bus has
> no sender until `PreToolUse` stamps one, which makes the gate its authentication
> layer rather than a reviewer bolted on top. And `SendMessage` cannot address a
> sibling without us supplying the agent id, so "routes without us in the loop" is
> not a capability it actually has here.

---

## Where this landed in the spec (rev. 4)

Applied, not proposed. `orchestrator-design.md` §0's "What you are not building"
lists things the SDK does *for* us; agent teams is a different category — a thing the
harness does *instead of* us, incompatibly — so it became its own subsection rather
than an append.

| Change | Section |
|---|---|
| The decision and its one structural reason | §0, "What you are deliberately not adopting" |
| Terminal states are not always terminal | §2.3 |
| Inter-agent messaging: the bus we own, `SendMessage`, cross-session | §2.7 (new) |
| Stop semantics gain a fourth axis | §9 |
| Message gating; the teammate-hook question | §9 |
| Work templates and the message bus | §8, step 8 |
| Diff table, sources, newly opened gaps | §10, §11 |

---

## Still unverified — close before relying on

> **Update 2026-08-12 (step 8).** Two of the four below are now closed by probes, and
> a third is closed as *moot*. Struck through where settled; see
> `2026-08-12-a-message-has-no-sender-until-the-gate-gives-it-one.md`.

- **Whether a settings-file hook inside a teammate process can reach our driver.**
  Would make a bridged gate conceivable and reopen the fork. Assumed no; not tested.
  This is the only finding that would change the decision above.
- ~~**Whether `SendMessage` should itself be approval-gated.**~~ **Moot: it is not
  adopted.** The question was answered for our own bus instead — gated on the send,
  because rejection needs a channel back and only the sender has one. `SendMessage`
  stays out of v1 for a reason found by probe rather than by argument: the CLI
  refuses a `subagent_type` and wants an agent id that appears only in the *lead's*
  context, so a worker cannot address a sibling unless we plumb the id into its
  prompt. It does not let the model route without us.
- ~~**What a sibling-roster send looks like on the wire from our side.**~~
  **Closed, and it was a live defect.** `scripts/verify_wake_path.py`: a woken
  sub-agent re-enters through a **second `SubagentStart`** carrying its original
  `agent_id`, ~7s after its own terminal `task_notification`, with a second terminal
  notification for the same `task_id` when it finishes again. The store did *not*
  already handle it — the driver emitted `AgentSpawned` for an existing node, which
  rebuilt the record and replaced the `Transcript` object readers hold under I7.
  Fixed as `AgentResumed`. There is no roster for plain sub-agents; the send is
  refused by name.
- **Whether the bundled CLI's cross-session messaging is enabled in our
  configuration.** Still open, and now lower priority — the in-process bus covers
  within-session routing, so this only matters when the pool needs to talk *across*
  sessions. It depends on feature-flag evaluation, which
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_TELEMETRY`, `DO_NOT_TRACK`,
  and `DISABLE_GROWTHBOOK` each turn off. `/list-agents` in a plain CLI session is
  the one-command check.

## Sources

Read 2026-08-11 against CLI 2.1.226:

- `code.claude.com/docs/en/agent-teams` — architecture, permissions, limitations
- `code.claude.com/docs/en/sub-agents` — sibling roster, auto-resume, depth limit,
  background tool filtering
- `code.claude.com/docs/en/cross-session-messaging` — sockets, inbound controls,
  availability
- `code.claude.com/docs/en/agent-sdk/hooks` — Python vs TypeScript hook matrix
- `code.claude.com/docs/en/agent-sdk/custom-tools` — in-process MCP server
