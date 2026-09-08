# Two mainline defects the autonomous-mode research exposed

**Dated:** 2026-09-04 · **Status:** both established, neither fixed; each needs a decision rather
than a measurement · **Origin:** found while researching
[`2026-09-03-a-dangerously-autonomous-mode.md`](2026-09-03-a-dangerously-autonomous-mode.md),
and filed separately because neither is about that mode

Both of these are live in the tree **today, at every policy, with no experimental mode enabled**.
They are recorded here rather than only in the mode record because a defect whose entire value is
being found later should be findable from the code it is about, not from the feature that happened
to expose it.

Symbol names, not line numbers, except where the line is the finding.

---

## 1. `model.relative_write` and `model.normalised_touches` are not in the same units

**The claimed invariant.** `ApprovedWrites.paths`' docstring: *"repository-relative and distinct,
in the order first written, so it can be compared against a declaration written in the same
units."* `relative_write`'s docstring: *"A declaration is repository-relative by construction."*
`bus.declare_task`'s schema string tells every lead to give paths *"relative to the repository
root."*

**What the code does.** `relative_write` produces a path relative to `AgentRecord.cwd` by stripping
it as a string prefix. `normalised_touches` states outright that it *"does not resolve a path
against a session's cwd."* The two sides are the same units only when `cwd` **is** the repository
root, and nothing establishes that: `model.LaunchSpec.cwd` defaults to `"."`,
`ui/launcher.LauncherState.spec` builds it as `self.cwd.strip() or "."` from a free-text
`input_text_with_hint` field, and it is not realpath'd on the launch path. `ui/projects._derive`
exists precisely because a cwd is not a repo root in general — it walks parents looking for `.git`.

The assumption lives in a test fixture rather than in the code:
`test_an_absolute_write_under_the_agents_cwd_is_recorded_relative` sets `cwd="/home/w/repo"`.

**Measured, not inferred.** `scripts/verify_declaration_units.py`, run 2026-09-04. It exercises the
pure functions directly, so the result is exact rather than observational.

| cwd | write | recorded as | matches `touches=["pptmstr/store.py"]` |
|---|---|---|---|
| `/home/w/repo` (control) | `/home/w/repo/pptmstr/store.py` | `pptmstr/store.py` | yes |
| `/home/w/repo/pptmstr` | `/home/w/repo/pptmstr/store.py` | `store.py` | **no** |
| `/home/w/repo/pptmstr` | `store.py` (relative) | `store.py` | **no** |

**Two failure directions, and the severe one is not the obvious one.**

**Loud, and automatic.** `Task.wrote_outside_declaration()` returns `('store.py',)` for an agent
that wrote exactly what it declared. Every write, whenever cwd is below the repo root, no operator
error required. Wrong and visible — and it discredits the one line the operator is meant to trust.
Today nothing surfaces it, because Item 3 of
[`2026-09-01`](2026-09-01-the-gates-measurement-outlives-the-approval.md) is unbuilt.

**Silent, and conditional.** `touches` is also the input to `store._auto_depends`, which its own
docstring calls *"the entire mechanism keeping two agents out of one file, mechanical and
unbypassable."* It compares normalised strings and nothing reconciles spellings. Measured: a task
declaring `pptmstr/store.py` against an existing task declaring `pptmstr/store.py` yields
`depends_on ('t-first',)`; the same physical file declared as `store.py` yields `()`. **No
dependency edge, both agents handed the file, nothing says so.** This needs two declarations in
different spellings, so it is conditional on declarer behaviour rather than automatic — but a
session running below the repo root is exactly the condition that makes an agent spell a path in
cwd units.

**Severity follows the silent one.** The loud failure is wrong about an observation nothing yet
reads. `_auto_depends` is load-bearing now.

**This is the 2026-09-02 amendment's failure class, one level up.** That amendment exists because a
units mismatch *"would have made the observation period measure nothing but itself."* It reconciled
absolute-versus-relative and left root-versus-subdirectory.

**Two candidate responses, both cheap. Not chosen here.**

1. Resolve the prefix once at launch with `git rev-parse --show-toplevel --show-prefix` and convert
   into a single unit. Fixes both directions.
2. Refuse to compute divergence when the session's cwd is not a repository root, and say so in the
   reading. Cheaper, and it is the discipline the 09-02 amendment already demands — but it does
   nothing for `_auto_depends`, which is the severe half.

**Not established:** whether any real session has ever been launched below a repo root. The
structure permits it; nothing in the tree records a historical cwd to check against.

---

## 2. The sub-agent cap does not count spawns made by sub-agents

**The finding, by reading.** `driver.py:1215` — the line is the finding —
`spawn = tool_name in ("Agent", "Task") and not agent_id`. The at-cap deny two lines later is
therefore skipped entirely for a spawn issued *by a sub-agent*, because `agent_id` is present for
exactly those calls.

**It is reachable in shipped configuration.** `templates.Role.tools` documents `None` as *"inherit
everything the session has"*, and `Role.tool_list()` returns `None` in that case, which
`driver._team()` passes to `AgentDefinition(tools=...)`. The `feature` template's **`builder` role
has `tools=None`** — every other role in `BUILT_IN` restricts to six tools; `builder` does not. So a
`builder` sub-agent can call `Task`, and that spawn is not counted against `subagent_cap`.

**Why it is bounded today, and what removes the bound.** With `Task`/`Agent` in `approval._REVIEW`,
such a spawn parks and a human sees it, so the cap being bypassed costs an approval rather than a
fleet. [`2026-09-03`](2026-09-03-a-dangerously-autonomous-mode.md) §8 decides that spawns
auto-approve under the dangerous policy, which **promotes `subagent_cap` to the only volume
control** — and a cap a sub-agent can spawn around is not a cap.

**The ordering above it is correct and worth preserving.** The cap deny runs *before* `classify`,
with the comment *"Ahead of classify, because a spawn that cannot be admitted must not reach"* it.
That is the pattern this codebase should keep — the invariant ahead of the policy, so no policy can
widen it. The defect is the predicate, not the placement.

**Note what is not recorded:** the comment explains the *ordering* and not the *exclusion*. Why
sub-agent spawns are outside the count is not written down anywhere, so it may be deliberate for a
reason that is no longer visible. Establish that before changing the predicate.

**Three candidate responses. Not chosen here.**

1. Drop `and not agent_id`, so every spawn counts against one cap.
2. Give roles explicit `tools` tuples excluding `Task` — tier-0 capability removal, which
   `notes/2026-08-31` §4.7 ranks above any gate, and the mechanism `READ_ONLY_TOOLS` already uses.
3. Both: (2) is per-template data an operator can edit, (1) is structural.

---

## Why these are not fixed here

The mode record's own reasoning applies: a fix that lands before the decision is a decision made by
whoever typed fastest. Both of these are one-line changes with recorded reasoning on the other side
— `_auto_depends`' exclusion is undocumented, and the units fix picks between a conversion and a
refusal that behave differently for the severe case. Each wants an owner, not a patch.

What is done is the measurement: defect 1 is falsifiable by re-running
`scripts/verify_declaration_units.py`, and defect 2 is falsifiable by reading the four symbols
named above.

---

## Amendment, 2026-09-05

Two agents investigated these before either was fixed. Both findings above survive; three
statements in them do not, and one of the corrections changes what the fix has to be.

### §2's claim that the exclusion is undocumented is wrong

This record says *"Why sub-agent spawns are outside the count is not written down anywhere."* It
is written down, in two places, and one of them is a test in the file the change would touch.

`planning/2026-08-14-a-role-runs-one-agent.md`, the record that introduced the cap, under *"The cap
as specified would have leaked"*:

> Nested spawns (`Agent` called from inside a sub-agent) are not counted, and today's behaviour is
> pinned by a test so the hole is visible rather than inferred. Widening `spawn` to cover them would
> corrupt the join to fix the count: a nested `SubagentStart` is not attributable to the parent's
> ledger entry.

And `tests/test_gate.py::test_a_spawn_from_inside_a_subagent_is_not_counted`, whose docstring says
it is *"Pinned because it is a hole in the ceiling rather than a decision that reads obviously from
the code."* Commit `6919662` carries the cap's own rationale.

**The reason has two halves and only one survives.** The justification — *"it still parks, so the
operator remains the bound on that branch"* — is exactly the premise
[`2026-09-03`](2026-09-03-a-dangerously-autonomous-mode.md) §8 deletes by auto-approving spawns.
The implementation half — widening the flag corrupts the join — is untouched and still binds.

**So option 1 above, read literally, is the change 08-14 refused.** `spawn` is one flag with three
consumers: the at-cap deny, `_expect_spawn` on the auto-approve return, and `_park(spawn=...)`.
Dropping `and not agent_id` counts nested spawns *and* admits them to the FIFO ledger that
`_take_spawn_tool_use` pops by `agent_type`. **The fix is to split the flag** — a cap-only
predicate at the deny, leaving `_expect_spawn` on the existing narrow one.

The clause also predates the cap: `planning/2026-08-13` quotes the same predicate guarding the
single-slot join, where `not agent_id` is straightforwardly correct. The cap was later layered onto
an inherited flag, which is why the code comment explains the ordering and not the exclusion.

### §1's silent half is not fixed by any write-side change, and the call graph proves it

`Task.touches` has one writer, the `TaskDeclared` arm. `relative_write` is reachable only from
`approved_write`, itself reachable only from the `ApprovalResolved` arm. **The two sets are
disjoint**, so any change to `relative_write`, `approved_write` or `ApprovedWrites` leaves
`_auto_depends` byte-identical and leaves the missed collision exactly where the probe found it.

Worse for this record's framing: the gap is already documented as an accepted limit.
`normalised_touches` states that not resolving against a cwd is *"a reason for the briefing to ask
for repository-relative paths rather than a reason to put a `Path.resolve` in a pure function."* A
fix has to argue against that reasoning, not around it.

**And the obvious form of the fix is worse than the defect.** Rebasing `Task.touches` in the
reducer cannot distinguish a repo-root-relative declaration from a cwd-relative one — both are
legal strings — so it would re-base *compliant* declarers and convert a conditional silent defect
into an automatic one. It would also break a stated property: `normalised_touches` promises the
stored tuple *"reads back as the declarer wrote it"*, and `board.BoardTask.touches` shows it to the
operator verbatim. The form worth considering instead is to widen the *comparison* in
`_auto_depends` — match on any candidate spelling — which is purely additive and can only add
edges, which is the direction that docstring already argues is safe.

**Say plainly what none of this closes.** `_auto_depends` is scoped by `belongs_to(session_id)`, so
by its own docstring *"two sessions in one working directory get no protection from this."* The
units gap is not the largest hole in it, and this record should stop calling that mechanism
unbypassable.

### A third defect, larger than either, found while investigating the first

`relative_write` returns `None` for **every** absolute write whenever the agent's cwd is not itself
absolute — `if not posixpath.isabs(base): return None`. `LaunchSpec.cwd` defaulted to `"."`, and
`Write`/`Edit` take an absolute `file_path`. So on the commonest launch every write landed in
`unplaced`, `Task.writes.paths` stayed empty, and `wrote_outside_declaration()` returned `()` for
the whole run. Items 1 and 2 measured nothing there.

Verified by execution and **fixed** in *A session is launched at a directory the store can resolve*:
the launcher and the `--task` path now resolve the cwd. This was cheaper than the subdirectory case
and strictly more severe, and it was invisible to the probe that found defect 1 because that probe
passes an absolute cwd in every row.

### Two probes now block, and neither existed when this record was written

1. **Does `SubagentStart` fire for a sub-agent spawned by a sub-agent?** `_outstanding_subagents`
   counts `_live_subagents`, populated only in `_subagent_start`. If a nested agent never appears
   there, then even with the cap predicate split the cap bounds the *burst* and not the
   *population*, and §8's "the cap is the only volume control" stays false after the fix lands.
   Nothing in `scripts/` exercises a nested spawn.
2. **Does the CLI grant `Task` to a sub-agent whose `AgentDefinition.tools` is `None`?** The
   pptmstr side of the chain is established — `feature`'s `builder` sets no `tools`, and
   `Role.tool_list()` returns `None` — but whether the CLI hands over the tool is a CLI behaviour
   nothing here measures. If it does not, defect 2 is unreachable in shipped configuration and the
   priority drops sharply.

### Both blocking probes answered, 2026-09-08

`scripts/verify_nested_spawn.py`, run against a team whose `builder` mirrors the shipped role
exactly — `tools=None`, which is what `Role.tool_list()` returns for it and what `driver._team()`
passes through. Reproducing the shipped shape was the point; granting tools explicitly would have
measured a configuration nobody runs.

```
PreToolUse      tool=Agent agent_id=<<absent>>        subagent_type=builder
SubagentStart   agent_id=ae36351d846b2afc1
PreToolUse      tool=Agent agent_id=ae36351d846b2afc1 subagent_type=general-purpose
SubagentStart   agent_id=a8462e25a13f97d9b
```

**Probe 2 — the CLI does grant `Task`/`Agent` to a sub-agent with `tools=None`.** The `builder`
issued a spawn call carrying its own `agent_id`, and the nested agent ran and replied. So defect 2
is reachable in shipped configuration, and `2026-09-03` §8d's *"very likely moot"* was wrong. The
`feature` template can fan out past its cap today.

**Probe 1 — `SubagentStart` does fire for a nested agent**, under its own `agent_id`, and
`_subagent_start` adds it to `_live_subagents` (`driver.py:1005`) with no parentage filter.

**This narrows the fix, and in the cheap direction.** The cap is not blind to nested agents:
once started, one occupies a slot in `_outstanding_subagents` exactly like any other. What is
missing is only the **admission** check — a nested spawn is never refused at the door, so the
population can be pushed past the cap, but it is not invisible afterwards.

So splitting the flag is **sufficient**. `2026-08-14`'s statement that nested spawns *"want a
separate counter"* does not apply to the cap half — occupancy already works — and applies only to
the join half, `_pending_spawns`, which splitting deliberately leaves on the existing narrow
predicate. The two halves of that record's reasoning come apart cleanly:

- **Cap:** widen the predicate at the at-cap deny. Nested spawns are counted at admission, and
  their occupancy is already correct.
- **Join:** leave `_expect_spawn` on `tool_name in (...) and not agent_id`. 08-14's objection —
  a nested `SubagentStart` is not attributable to the parent's ledger entry — is untouched by this
  change and stays true.

**What this does not establish.** The probe ran one nested spawn, not a burst, so the interaction
between nested admissions and the `_pending_spawns` ledger under concurrency is unmeasured. And it
observed the CLI, not pptmstr's gate — the events are what `AgentSession` would receive, but the
run did not go through `_gate_tool_use`.
