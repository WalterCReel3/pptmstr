# A session can be named, and the name is its identity

**Dated:** 2026-08-11 · **Status:** planned — not started · **Refines:** the
label rule in `2026-08-10-research-sessions-under-the-inbox.md` §1 and
`2026-08-10-layout-proposals.md:283-285`

The **New Task** modal grows an optional name field. Where a session is currently
identified by its opening prompt, it is identified by its name when it has one and
by the prompt when it does not.

## What prompt-as-identity costs

The rule that a row is titled by its task was right against the alternative it
displaced — every root is called "session", so `agent_type` gave twenty sessions
six identical rows. It is still right against that alternative. The cost it carries
is different: a prompt is written to be *complete*, and identity has to be *short*.
Those pull opposite ways and the prompt was asked to do both.

The evidence is in the code written to cope with it. `widgets.ellipsis` exists
mostly for this one field (`widgets.py:344-346`), and every root-identity site
calls it — `rail.py:223,239`, `inbox.py:199`, `inbox.py:510`, `health.py:69`. The
rail budgets the title arithmetically (`rail.py:236-238`, `width` minus padding
minus badge minus rain gap) because the string will always be longer than the room.
A well-written opening prompt is a paragraph; three densities of card and a 320px
inbox line are all clipping the same paragraph to its first few words, and the first
few words of a prompt are usually its least distinguishing part — *"read the store
and…"*, *"read the driver and…"*.

## Why this is not the fallback that was already rejected

`…under-the-inbox.md:46-59` rejected a change of exactly this shape and did so on
grounds worth restating rather than skipping:

> The old candidate was a fallback — use `rec.task` *when* `agent_type` is `None`.
> … Task is not a fallback for a missing category, it is the identity. That
> generalises where the fallback does not.

`name or task` is a fallback expression, so the objection has to be answered
directly. It is answered by what the two candidates are falling back *from*.

The rejected fix fell back from a **derived** string to a stated one — `agent_type`
is assigned by the machinery, and the doc's complaint was that deriving identity
from a category produces rows that cannot be told apart. Naming falls the other
way: from a string the operator wrote *about this session specifically* to one they
wrote about the work in general. Both are stated; the name is stated more
specifically. The rule the earlier doc was defending survives intact and gets
sharper:

> **Identity is what the operator said this session is. The task is what they said
> when they said nothing else.**

That also explains why the name is optional and stays optional. Requiring it would
make the common case — one quick session, dispatched and watched — pay a field for
a benefit that only appears at N sessions across M projects. The prompt remains a
perfectly good identity for a fleet of three.

## The name is a fact, not a presentation

It goes on `AgentRecord` next to `cwd`, and `cwd`'s own comment
(`model.py:355-360`) is the precedent: the store holds the fact chosen at launch,
and the UI decides what to make of it. A name is chosen at launch by a human, which
is as factual as this system gets. Nothing about it is derived, so the objection
that put project-naming in `ui/projects.py` instead of the store does not apply.

There is no persistence question. `AgentRecord` and `Snapshot` are memory-only —
`settings.py` is the only serializer in the codebase and its docstring says these
never enter a snapshot — so there is no schema to migrate and no compatibility
surface. This is the cheapest moment this change will ever be available at.

## One rule, in one place

`AgentRecord.label` — `self.name or self.task` — and the eight identity sites read
it. Not `rec.name or rec.task` written out eight times, for the same reason
`inbox.identity()` is public and DETAIL calls it (`inbox.py:144-147`): two panes
naming the same thing by different rules is the defect the single cursor exists to
prevent, and the ninth site nobody has written yet needs somewhere to find the rule.

The property also collapses the sub-agent branch. `_subagent_start` sets a
sub-agent's `task` to its `agent_type` (`driver.py:521`) and a sub-agent never has
a name, so `agent_type or task` and `name or task` agree on every sub-agent that
exists. `rail.py:319` and `health.py:116` become `sub.label` with no branch on
`parent is None`, which is the same convenient collapse `…under-the-inbox.md:58-61`
noted for the original rule.

## What the name does not replace

Three sites read `task` and must keep reading `task`, because their audience is not
the operator scanning a rail:

- **Clipboard export** (`detail.py:279-280`). Its first line goes into a bug report
  or a message to someone else, and *"auth audit"* means nothing outside this
  screen. Export prints the name as the line and the task beneath it when both
  exist — the one place they are worth the two lines.
- **The transcript** (`driver.py:710,780`). `send(self.task)` echoes the prompt into
  the SESSION pane as a SYSTEM segment. This is what makes the change safe: the full
  prompt stays one pane away from every place it stops being the title, so naming
  hides the prompt from the rail without hiding it from the operator.
- **Logs** (`app.py:261`, `pool.py:72`). These print a prefix of the task. They gain
  the name — correlating a queue entry to the thing you named is most of what the
  log is read for — but they keep the task, because a log line read a day later has
  no snapshot to look the name up in.

## Fork and relaunch carry it

`health.py:145` and `inbox.py:430` re-dispatch from `record.task` through
`Callable[[str, str, str], None]` (`health.py:39`, `inbox.py:82`). Both go to
4-arity and pass `record.name`. A relaunch that dropped the name would revert the
session to a wall of prompt text at the exact moment the operator is most annoyed
with it, which is the failure this document exists to fix, reproduced inside its own
fix. The habit is familiar enough here to be worth naming.

## Deliberately not in scope

- **Rename after launch.** Wanted, and cheap later — one `SessionRenamed` intent and
  one `_apply` case, since the store centralises every mutation. It is a second UI
  surface (where does the affordance live? the rail card? DETAIL?) and this change
  should be one concern. Note the ordering: naming at launch is a guess made before
  the work starts, so rename is the feature that makes naming *reliable*, not a
  nicety. Expect to want it within a day of using this.
- **A derived or suggested default name.** Summarising a prompt into a title is a
  model call on the launch path, and `topic`'s comment (`model.py:346-348`) records
  why that boundary is held: fields visible every frame are never produced by
  summarisation. An empty name field falling through to the task is free and
  truthful.
- **`Enter` in the name field.** The launcher submits on `Ctrl+Enter`
  (`2026-08-11-prompt-boxes-send-on-ctrl-enter.md`), and giving a single-line name
  box `enter_returns_true` would make `Enter` mean "launch" one field above where it
  means "newline". The name field is inert on `Enter`; `Ctrl+Enter` launches from
  either field.

## Three comments and one test name become false

The repo's rule is that comments describe the code as it is, so these get rewritten
rather than annotated:

- `widgets.py:344-346` — *"the task, which is the only string that distinguishes one
  session from another"*. That sentence is the justification for `ellipsis`
  existing, and it stops being true.
- `rail.py:234-235` — *"The task, not the node name. Every root is called
  'session'…"*.
- `inbox.py:141-142` — *"the session's task is the headline and the sub-agent's name
  rides alongside it"*. Note the collision this change creates in the vocabulary:
  this docstring already uses "name" for the sub-agent's `agent_type`, and
  `rail.py:234` uses it for the node name. Both should say what they mean once
  `name` is a field.
- `tests/test_inbox_rail.py:84-105` — `test_a_row_is_titled_by_the_session_task`.
  This is the earlier decision written as an executable assertion, which is what
  makes it the moment the change actually lands. It becomes titled-by-label, keeps
  its existing body as the unnamed case, and gains a named one.

## Checklist

1. `model.py` — `name: str | None = None` on `AgentRecord`; `label` property.
2. `intents.py` — `name` on `AgentSpawned`; `store.py:131` threads it. Store test.
3. `driver.py` — `AgentSession.__init__(..., name=None)`; `announce()` passes it.
4. `app.py:251` — `_launch(..., name)`; log line gains the name.
5. `ui/launcher.py` — `name` on `LauncherState`, single-line field above the task
   box, `spec()` returns 4. Only `test_spec_strips_task_and_resolves_model`
   (`tests/test_launcher.py:60-62`) asserts the whole tuple and breaks; the rest
   index into it and survive. That asymmetry is a reason to keep the assertion
   whole-tuple rather than relax it — it is the one test that notices the shape
   changed.
6. Read sites to `label`: `rail.py:223,239,319`, `inbox.py:152,510`,
   `health.py:69,116`, `detail.py:112`. `inbox.py:199` and `detail.py:122,134` come
   free through `identity()`.
7. Fork/relaunch to 4-arity; `detail.py:279-280` export prints both.
8. Comments and test names above.

Steps 1–5 are independent of 6–8: the field can land and be inert, and the read
sites are the only part that is a judgement call.

## Open question

Whether `label` belongs on `AgentRecord` at all, or whether the identity rule should
sit beside `inbox.identity()` with the record holding only the two facts. The case
for the record is that `rail.py` and `health.py` do not go through `identity()` and
would otherwise import from `ui/inbox.py` to title a card. The case against is that
it puts a presentation rule on a store type, which is the line `cwd` was careful not
to cross. Decided for the record on reach, not on principle — worth revisiting if a
second projection over the same two fields ever appears.
