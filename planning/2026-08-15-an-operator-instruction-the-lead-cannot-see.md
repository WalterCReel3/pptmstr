# An operator instruction reaches one worker, and the spec it changed has no second reader

**Dated:** 2026-08-15 · **Status:** open, no code changed · **Found by:** dogfooding
the splash panel work · **Precedent:**
[`2026-08-12-a-message-has-no-sender-until-the-gate-gives-it-one.md`](2026-08-12-a-message-has-no-sender-until-the-gate-gives-it-one.md),
whose `Concern.edited` fix is the same defect class in the other channel.

Symbol names, not line numbers, per the 08-14 convention.

This is not part of the splash feature. It is what the splash session exposed, and it
is the more valuable output of the run.

## What happened

The operator redirected a builder mid-task: the art belongs in the NEEDS YOU pane, not
DETAIL, and `ui/detail.py` should not be touched. That superseded the spec the builder
had been given when it claimed its task. The builder declined to build the superseded
version and escalated to the lead saying an operator instruction had changed it.

The lead had no record of any such instruction — not in the task it had declared, not
in any concern it had sent or received. Checking what it did have, it found the phrase
"Consider the NEEDS YOU pane" inside a *different* builder's concern: a proposal
addressed to the lead, one of three options, not a decision. From that it concluded the
builder had promoted a suggestion into an order and attributed it to a person, and told
it so at length. The operator intervening directly was the only thing that established
the builder had been right throughout.

Every step is locally correct. The lead really had no record. An unattributable
instruction really is a thing an agent should refuse to act on. And a near-miss phrase
really was sitting in the lead's own inbox, which is what turned a plausible
explanation into a confirmed one. **A design that makes a confident wrong reading
available is worse than one that makes the question hard**, because nobody goes looking
for an answer they believe they already have.

## The bug is not that the lead was not copied

Framing this as message delivery gets a fix that does not hold: add a CC, and the
lead's record is now a second copy of the spec that agrees with the operator's only as
long as every future redirect remembers to use the copying path. That is the shape
`STYLE.md` §1 names — a stored duplicate of a fact, kept true by whoever remembers to
update it — and it is the shape this codebase's defects have historically come from.

The accurate statement is: **a spec has two authors and one record, and the record is
not reachable by the author who did not write it.** Fix where the authoritative spec
lives, not who gets told about changes to it.

## Where a spec actually lives today

Three copies, and the code path for each is different:

- **`Task.detail` in the store.** Written once by `declare_task`, never afterwards
  (below). This is the closest thing to an authoritative spec that exists.
- **Text in the claiming worker's context.** `bus.py`'s `claim_task` interpolates
  `won.detail` into the reply string at the moment of the claim. From then on the
  worker is working from a transcript copy that nothing can invalidate.
- **A prompt in one session's transcript.** An operator redirect goes through the
  reply box (`ui/compose.py` → `SessionPool.send` → `AgentSession.send`), which is an
  ordinary prompt into one session. It appends the text to *that session's* transcript
  and emits `StateChanged(THINKING, topic="reading your message")`. No intent carries
  its content, so it reaches no task, no concern, and no record a second agent reads —
  the words exist, in the one place only their recipient looks.

The board pane does not close the gap either: `ui/board.py`'s `BoardTask` carries `id`,
`title`, `state`, `blocked_on` and ownership — **no `detail` field**. `grep -rn '\.detail'
pptmstr/ --include=*.py` finds one read of `Task.detail` in the whole application, and
it is the `claim_task` interpolation above. So the spec is write-once, read-once, by a
single agent, and no surface displays it. Even the operator cannot read back the
instruction the builder is holding.

## The asymmetry is in the gate, and only one side of it was designed

`approval.py` puts `post_concern` in `_REVIEW` and the other five bus tools in
`_BUS_AUTO`. The comment on `_BUS_POST` gives the reason and it is a good one: a
message between agents is gated at the *send* so that a rejection has somewhere to go.
The consequence is that **all agent-to-agent traffic passes under the operator's eye
before it lands**, and the operator's model of the plan is continuously updated by
work it is already reviewing.

There is no corresponding path in the other direction. The operator can change what a
worker is building without any of it reaching the agent whose job is to hold the plan.
The asymmetry is not an oversight in the gate — the gate is doing exactly what it was
built for — it is that operator→worker was never modelled as a channel at all. It is a
prompt, and prompts are per-session by construction.

## The precedent, and whether its fix should have generalised

`Concern.edited` exists because of the same class of event. An operator's rewrite of a
concern reached the handler as ordinary arguments, and a `Concern` built from those
arguments alone kept the edited text with no record that it had been edited. The fix
was `EDITED_KEY = "_edited"` in `bus.py`: a stamp the model cannot set, applied where
the operator's intervention crosses into the store.

Stated plainly: **the fix did not generalise, and it could not have.** It works because
a concern is already a store object that passes through a handler the operator's edit
flows through, so there was a seam to stamp. An operator prompt has no such seam — it
is not a tool call, it does not pass the gate, and there is no record it is an edit
*of*. The generalisation is not "stamp this too"; it is the principle underneath, which
is that **an operator intervention that changes what an agent does must leave a mark in
the store, not only in a transcript.** That principle should have been written down as
a rule when `Concern.edited` was built, and this document is where it gets written
down. `Concern.edited` satisfies it for one channel. The redirect channel does not
satisfy it at all.

## Should an operator redirect amend the board task?

**Position: yes, `Task.detail` should be the authoritative spec and a redirect should
amend it.** That is `STYLE.md` §1's "derive, do not store" applied to coordination — if
the spec is in one place, there is no second copy to disagree, and the lead's model
stops being a thing that can silently diverge because it stops being a separate thing.

Two obstacles were checked rather than assumed, and both are real:

**`declare_task` cannot be the amendment path.** The `TaskDeclared` arm in `store.py`
ignores a re-declared id outright, and its comment gives the reason: the only way it
happens is a retry, and overwriting would silently unclaim work somebody is doing. That
reasoning is correct and should not be weakened to make room for amendment. An
amendment is a *different* operation — it targets a task that is expected to exist and
expected to be claimed — and needs its own intent, with `node_id=None` marking it as
the operator's, the way `ConcernEdited` already does.

**Amending alone does not fix the case that occurred.** `claim_task` copies `detail`
into the worker's context once, at claim time. A task amended afterwards changes the
board and reaches nobody: the builder in the middle of the work is the one reader who
must see it and the one reader who has already read. So the amendment intent is
necessary and not sufficient. Whichever of these follows it, both are open:

- the claimed spec is re-read rather than remembered — the worker asks the board what
  it is building instead of trusting its transcript copy, which is the honest reading
  of "derive, do not store"; or
- the amendment is delivered, as a concern from the operator to the current claimer
  *and* to the task's declarer, which reuses the channel that already works and accepts
  that the transcript copy is a cache with an invalidation message.

The first is structurally stronger and costs a discipline the workers do not currently
have. The second is buildable on what exists today. Not resolved here.

## What the worker did right, and whether it should be the rule

The builder **held the task claimed** rather than releasing it, reasoning that
releasing would hand the superseded spec to the next builder. Releasing is the normal
rule when a worker cannot proceed, so this was a deliberate departure and it was the
right one.

It is right for a checkable reason. The `TaskReleased` arm in `store.py` sets state
back to `PENDING` and clears `claimed_by` — and touches nothing else. `detail` is
unchanged. So a release does not merely fail to fix the divergence; it *propagates* it,
silently, to an agent with no reason to suspect anything, and it destroys the only
signal that something was wrong, which was that a task sat claimed and unmoving.

**Position: hold, and escalate, is correct, and it should be documented — as a
consequence of the board being uncorrectable rather than as a standing exception.** The
rule is: *do not release a task whose spec you believe is superseded, because the board
carries the spec and releasing republishes it unchanged.* If the amendment path above
lands, the rule inverts to the better one — get the board amended, then release — and
holding stops being necessary because releasing stops being harmful. Writing the rule
with its reason attached is what lets it be retired when the reason lapses.

## What it cost

A builder sat blocked across three claim cycles. A lead spent a message accusing a
correct agent of fabricating an instruction, and the accusation was detailed and
confident, because the false explanation was the only one its evidence supported.

Both are cheap at this size. Neither is cheap at scale, and the second is the one to
weigh: the mechanism that surfaced the divergence presented it as worker error. An
agent that is punished for correctly refusing a superseded spec learns to stop
refusing, and the failure after that one is silent.

## Not doing

- **Adding a CC of operator prompts to the lead.** Treated above: it makes the lead's
  record a second copy that agrees by convention.
- **Relaxing the `TaskDeclared` re-declare guard.** Its recorded reason holds; an
  amendment is a different operation and needs a different intent.
- **Any code change in this pass.** The defect is described here and the fix is not
  decided — only the two obstacles it has to clear.
