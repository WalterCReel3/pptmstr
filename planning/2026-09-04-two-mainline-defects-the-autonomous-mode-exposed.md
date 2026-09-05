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
