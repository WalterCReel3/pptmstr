# What a worker is given

**Dated:** 2026-08-17 · **Status:** superseded by
[`planning/2026-08-17-a-session-premise-is-a-place-not-a-message.md`](../planning/2026-08-17-a-session-premise-is-a-place-not-a-message.md),
which carries the decisions; this file is kept for the observations they were derived
from · **Probe:** `scripts/verify_worker_context.py` · **Found by:** dogfooding, three
reports from the operator

**This is not a planning record.** Nothing here is a decision, and nothing here should
be read as one. `planning/` holds dated scope snapshots for work not yet started, and
its documents settle things; this file only says what was checked, what it turned out
to be, and what still cannot be answered by reading. When something here does get
decided, the decision belongs in `planning/` and this file becomes redundant.

Symbol names, not line numbers, per the 08-14 convention.

---

## The three reports

1. **Sub-agents on teams are not seeded context.** Settled decisions get relitigated,
   research points rehashed, approaches reworked — all of them already settled in the
   session's originating document.
2. **Sub-agents complain they cannot list tasks.** Board state awareness is weak.
3. **Sub-agents re-test, re-format and re-analyse in flight** rather than working
   towards one pipeline that catches everything at once.

Report 2 is literally true and is a gap in the original specification. Report 3 is
fully diagnosed and fully recorded already, and every one of its fixes is unbuilt.
Report 1 is the new one, and it is the only one that needs a measurement before it can
be designed.

---

## The fact that frames all three

`pptmstr/bus.py`, `templates.py`, `approval.py`, `model.py` and `store.py` were last
touched **2026-08-15 08:22** — before the run that produced
[`planning/2026-08-15-what-the-board-does-not-carry.md`](../planning/2026-08-15-what-the-board-does-not-carry.md).
Nothing in the coordination layer has changed since. None of that document's prose
fixes (its items 1, 6, 7, 8), the `format-file` target (item 4), the terminal *green
the gate* task (item 5), or the read primitive (item 2) exist in the tree.

Its own item 11 predicted this: *"a planning document is necessary and demonstrably not
sufficient."* Two of the three reports above are that prediction landing, and that is
the reason this file ships beside a probe rather than as another open record.

---

## What is structurally certain, from reading alone

### A worker's context has exactly four sources

`_team` in `driver.py` builds one `AgentDefinition` per role, setting `description`,
`prompt`, `tools` and `model`. `templates.worker_prompt` supplies that prompt: the
role's own prose plus the fixed "Working with the team" block. It carries **no project
context, no session premises, and not one word of `lead_briefing`.**

Everything a worker can know therefore arrives by one of:

| Channel | Written by | When |
|---|---|---|
| `worker_prompt(role)` | us, at build time | spawn |
| the `Agent` tool's `prompt` argument | the lead, freehand | spawn |
| `claim_task`'s reply, interpolating `Task.detail` | the lead, at declaration | once, on claim |
| `read_inbox`, rendering concerns | any agent | during the run |

Plus the CLI's own scaffolding, whose contents are the open question below.

### The originating document reaches a worker only if the lead retypes it

`AgentSession.task` is used in three places: `announce()` puts it on the card,
`run()` sends it as the lead's first user message, and `pool.py` logs it. Sub-agents
do not share conversation history, and `initialPrompt` is unset. So the *only* paths
from the session's premises to a worker are the lead re-expressing them in a spawn
prompt or in a `Task.detail`.

A worker relitigating a settled decision has not ignored the document. It has never
seen it, and nothing tells it one exists.

### There is no originating document — there is a string in a text box

`LauncherState` carries four fields' worth of launch spec: `task: str` (a multiline
box), model, cwd, template; `spec()` returns that tuple, and `app._launch` hands it to
`AgentSession`. No file argument, no path, no attachment; the headless path takes
`args.task` strings from argv. `AgentRecord` stores `task: str` and no document, no
URI.

Note the vocabulary collision: `AgentRecord.deliverable` already means the opposite
thing — a finished session's *output*. Whatever an input document gets called, it is
not that.

**Before a worker can re-read settled premises, the premises have to be an object.**
Today they are a line in one agent's transcript.

### No agent can see the board

`build_server` exposes six tools: `post_concern`, `read_inbox`, `claim_task`,
`declare_task`, `complete_task`, `release_task`. None reads the board. `claim_task` is
the only board read, it mutates, and it returns only the task it just handed you —
and per the 08-15 document's item 2, it will not even re-read a task you already hold,
because `_pick_claim` requires `is_claimable` and that requires `PENDING`.

`orchestrator-design.md` §2.7 specified three tools and no board read, so this is a gap
in the original design rather than a regression. The projection a read tool would need
already exists and is already tested: `ui/board.py:board_tasks`. The reply channel it
would need also already exists — `bridge.ask` plus an effect, the shape STYLE.md §1
("when the core must answer, widen the return") blesses and that `claim_task` and
`read_inbox` already use.

One real design question sits inside the cheap fix: `board_tasks` lives under `ui/`,
and the bus reaching into the UI layer is wrong. The projection wants to move down, or
the derivation gets duplicated — and STYLE.md §1 is explicit about what a duplicated
derivation costs.

### The operator's only write path into a worker closes at spawn

`approval.classify` puts `Task`/`Agent` in `_REVIEW`, so every spawn parks, and
`ui/review.py`'s `pretty_args` renders the whole raw-args blob as editable JSON,
delivered back through `updatedInput`. That is a real seeding lever, and it is the
only one — it is gone the moment the worker starts:

- `ui/compose.py:draw_conversation` refuses: *"sub-agents cannot be messaged directly.
  talk to the session that spawned this one."*
- `pool.SessionPool.session_for` maps any sub-agent `NodeId` back to
  `(session_id, None)`, so even without that guard the text would land in the lead's
  conversation.
- `SendMessage` is out of v1
  ([2026-08-12](../planning/2026-08-12-a-message-has-no-sender-until-the-gate-gives-it-one.md))
  and unusable anyway.

The one exception: a gate denial's free-text reason reaches the calling agent as
`permissionDecisionReason`. So while watching a worker run the gate for the third time,
the operator's only in-flight channel is rejecting the call and saying why. That says
*no, because*. It cannot say *batch it*. An adversarial, per-call channel is being
asked to carry a scheduling instruction, which is why the behaviour feels unstoppable
rather than merely wrong.

### Nothing tells a worker when to verify

Grep `templates.py` and `driver.py` for the gate, tests, lint, format, or any
verification cadence: nothing. The only cadence instruction in `worker_prompt` is
"read your inbox when you start and again whenever you finish a piece of work", which
is a per-piece cadence.

---

## Why report 3 happens

Four mechanisms, in order of how much they explain.

**1. There is no policy, so the default fills the vacuum.** pptmstr states no
verification policy to a worker at all, and Claude Code's own disposition is
verify-after-each-change.

*But the obvious fix is one this repository has already argued against.*
[`2026-08-15-a-task-reaches-the-board-without-a-decision.md`](../planning/2026-08-15-a-task-reaches-the-board-without-a-decision.md)
§"Why the guard does not go in the briefing prose" says: *"`CLAUDE.md`'s 'How to think
here' has seven rules. Four push toward more work… The document is monotonically
pro-rigor, so a new paragraph asking for restraint is outvoted by construction… Prose
is the weakest mechanism available and it is the one we reach for because it is the
cheapest to write."* "Verify less" is precisely a restraint instruction. On the
recorded reasoning it loses.

**2. The task is the unit of completion, and completion is load-bearing.**
`complete_task` unblocks dependents, so a worker cannot mark done without believing
done. With no shared notion that verification happens later and belongs to someone
else, every task carries a full verify cycle: N tasks, N gate runs. The structural
answer is item 5's terminal *green the gate* task sequenced by `depends_on` — which is
lead discipline, and was never written into `lead_briefing`.

**3. Blindness makes defensive verification correct.** A worker cannot see the board,
cannot see its peers, cannot know whether a terminal gate task exists, and knows (item
10) that a red may be someone else's mid-write. Under those constraints re-running is
the rational move. **Most of report 3 is report 2's symptom.**

**4. It is partly the recorded fixes working.** Item 1's rule is *confirm a reported
defect still exists before fixing it* — that is re-analysis. Item 10's rule is *re-run
a red before reporting it, and name the file* — that is re-testing. Both are right.
Both were written as staleness protection with no cost budget attached. Some of what
reads as over-eagerness is competent workers deriving those rules unaided.

Report 3's own instance of self-amplification: `make format` is
`black pptmstr scripts tests`, still the only writing target in the Makefile. One
worker formatting touches every file; the next sees churn it did not cause and
re-analyses. Four lines of Makefile, recorded on 08-15, not applied.

---

## The tension a fix for report 1 must not break

`what-the-board-does-not-carry.md` is organised around this finding:

> **a version of this team that followed instructions faithfully would have shipped
> worse work than one that argued.**

Four agents refused or corrected a spec they were handed and every one was right. The
`worker_prompt` line that produces it is called out as *"doing more load-bearing work
than any mechanism in `store.py`"*.

Report 1 asks for less relitigation. That document says arguing is the safety property.
They are not the same thing, but they are close enough that a blunt fix hits both. The
distinction:

- **Arguing from evidence** — the worker read the tree, the spec describes code that is
  not there, it refuses. This is the property. Keep it.
- **Arguing from absence** — the worker rederives a question that was settled in the
  originating document, because it has no path to that document and no way to know one
  exists. This is waste.

The second looks like the first from outside, which is how it survived a whole
post-mortem without being named. Any seeding mechanism has to make settled premises
*reachable* without making a handed-down claim *authoritative* — item 6's rule (*a
finding you did not verify goes into a task as a finding to check, not an instruction
to carry out*) is the same constraint seen from the lead's side.

---

## Three recorded refusals that constrain the fix

Not re-litigated here; listed so a proposal can be checked against them.

1. **No second copy of a spec.**
   [`an-operator-instruction-the-lead-cannot-see.md`](../planning/2026-08-15-an-operator-instruction-the-lead-cannot-see.md)
   refuses "adding a CC of operator prompts to the lead" on STYLE.md §1 grounds — a
   stored duplicate kept true by whoever remembers to update it, named as this
   codebase's historic defect shape. **Pasting the originating document into every
   worker's prompt is that shape exactly.** The fix has to be nearer *reference* than
   *copy* — which makes report 2's read primitive a prerequisite for report 1 rather
   than a sibling of it.
2. **Prose does not bind.** Quoted above. It applies to any fix whose whole content is
   a new paragraph in `lead_briefing` or `worker_prompt`.
3. **A declaration must not hold a plan.** *"Work that arrives with a remedy attached
   reads as already-decided and invites approval — and the plan is the part that goes
   stale."*

---

## One conflict, flagged and not settled

Two documents written the same day disagree, without citing each other, on whether the
lead should declare a task from `planning/` at session start.
`what-the-board-does-not-carry.md` makes it item 0 — the single process change that
drains the backlog rather than adding to it. Its same-day sibling
`a-task-reaches-the-board-without-a-decision.md` argues that is the backlog burst it
exists to warn about, and flags the conflict in its own "Open" section: *"One of the two records is wrong, and they were
written the same day without citing each other."*

This matters here because "the lead declares from `planning/`" is the nearest existing
mechanism to what report 1 wants, and half the record says it is the problem. Recording
the contradiction is not resolving it, and it is not resolved here.

---

## The measurement

Four probes exist for sub-agents — `verify_subagents.py`, `verify_subagent_usage.py`,
`verify_message_bus.py`, `verify_wake_path.py` — and every one measures the *outbound*
direction: hooks, identity, usage, addressing. **Not one has ever measured a
sub-agent's inbound context.** Four probes about sub-agents, zero about what a
sub-agent was given.

`scripts/verify_worker_context.py` is that probe. It plants one nonce per channel in a
temp workspace, denies every tool that could reach a nonce another way, and has each
worker answer through a probe-owned MCP tool so what is captured is a tool call rather
than a sentence about one.

| # | Question | Method |
|---|---|---|
| 1 | Does project CLAUDE.md reach a worker? | `MEMORY_CANARY` in a temp-cwd `CLAUDE.md`; two arms, `memory` unset vs `memory="project"`; the root answers as a control |
| 2 | Does the lead's briefing leak to a worker? | `BRIEFING_CANARY` in the system-prompt append. Negative control — an expected NONE |
| 3 | Does the sibling roster reach a worker, conditional on `SendMessage`? | Nonce-suffixed role names; a worker naming a *sibling* is proof. `tools=None` vs an explicit list |
| 4 | Does a worker hold `Skill`? | One attempted call, observed at PreToolUse |

Question 2 is the one that makes the others readable: if a worker reports the briefing
canary, the method cannot discriminate and questions 1 and 3 are void. The probe says
so in its own output rather than leaving it to the reader. Question 4's negative is
inconclusive by construction — a worker without the tool and a worker declining to call
it look identical — and it is labelled that way rather than reported as an answer.

### Results — run `04e4db`, 2026-08-17, `claude-sonnet-5`

The negative control passed first: **all four workers answered NONE to the briefing
canary**, so the lead's `lead_briefing` append does not reach a worker, and the method
discriminates. Questions 1 and 3 are therefore readable rather than void. The four
spawn prompts were each exactly `begin`, with no canary leakage. No worker reached for
a file tool — the denial list holds one `ToolSearch` from a worker and `Bash`,
`ToolSearch` and `ScheduleWakeup` from the root, all refused — so no canary arrived by
reading.

**1. Project CLAUDE.md reaches a worker, with `memory` unset.** Every one of the four
workers reported `MEM-39ecc220b356`, as did the root. `memdefault` (field unset) and
`memproject` (`memory="project"`) are indistinguishable, so **`memory` is not the
lever** — the canary already arrives without it. Whether the field does anything else
is unmeasured and was not the question.

**3. The roster is conditional on the tool grant, and it is a list of agent *types*.**

| Role | Tool grant | Answer |
|---|---|---|
| `rosterbroad` | `tools=None` (inherits everything) | named `memdefault`, `memproject`, `rosternarrow`, plus `claude`, `Explore`, `general-purpose`, `Plan`, `statusline-setup` |
| `rosternarrow` | explicit list, no `SendMessage`, no `Agent` | NONE |

The named set matches the init message's `agents` array exactly, built-ins included —
so what a worker receives is the **registry of agent types it could spawn**, not a
roster of the agents actually running. It says which roles exist; it does not say who
is live, and `post_concern` addressing is not what it answers.

**One attribution this probe cannot make.** It varied the whole tool grant, and both
`SendMessage` and `Agent` differ between the two arms. Design §2.7 credits
`SendMessage`; the registry's content — spawnable types — points at `Agent`. Which one
gates it is *not settled here*. For pptmstr's own roles the conclusion is the same
either way, because `READ_ONLY_TOOLS` excludes both.

**4. Unanswered, as designed.** No `Skill` call reached the gate, which is consistent
with the worker not holding the tool and with it holding the tool and declining. The
root's init reports `has_skill_tool: true` across 31 tools and 16 skills, which says
nothing about a worker.

**Two init messages arrived, and they are identical** — same cwd, same `agents`, same
31-tool count — so neither is per-agent. A worker's context is not readable off the
wire; asking it is the only way. Also worth recording: the field is `memory_paths`, not
the SDK TypedDict's `memoryFiles`, and it holds only the auto-memory directory
(`~/.claude/projects/…/memory/`). It does not enumerate CLAUDE.md, so it is not the
evidence for question 1 — the canary is.

### What this settles, and what it moves

**Reference-based seeding has a substrate.** Workers read project memory today, so
pointing one at premises that live on disk needs no new plumbing and does not create
the second copy the record refuses. That was the best of the three possible outcomes.

**But it sharpens report 1 rather than solving it.** CLAUDE.md is *static project*
context. In the real app a worker therefore already gets the repository's CLAUDE.md,
and through it the pointers to STYLE.md and `planning/` — and it is still relitigating.
The reason is now precise: **the settled decisions the operator means are not in
project memory. They are in the launch text box**, held as `AgentRecord.task`, in one
agent's transcript, reachable by nothing. The gap is not that workers cannot read; it
is that there is nothing for them to read.

**And the review roles are the blind ones.** `reviewer`, `investigator` and `skeptic`
carry `READ_ONLY_TOOLS` and get no registry at all. The agents whose entire value is
disagreeing are the agents told least about who else exists — which stacks on the
08-15 document's item 8, where instance addressing is documented to the lead and never
to a worker.

---

## Not decided here

- Whether a launch spec becomes an object, and what it is called (`deliverable` is
  taken, and means the opposite). The measurement makes this the load-bearing
  question for report 1: the reading side works, the written-down side does not exist.
- Whether the registry is gated on `SendMessage` or on `Agent`. The probe varied both
  at once and cannot separate them.
- Whether the review roles should get the registry, and at what cost — the grant they
  lack it through is `READ_ONLY_TOOLS`, chosen for a reason the 08-15 record still
  endorses.
- Whether the board read is narrow (`task_detail(task_id)`, which the record endorses)
  or general (`list_tasks`, which is wider than anything recorded and needs its own
  argument).
- Where `board_tasks` lives once something outside `ui/` needs it.
- Which of the two same-day records is right about declaring from `planning/`.
- Anything about report 3. Its plan is written, ordered and unbuilt; this file adds the
  *why*, and adding a second plan beside the first is what item 11 warns against.
