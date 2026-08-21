# A dead session costs more than its approvals

**Dated:** 2026-08-21 · **Status:** diagnosis and proposals, none built

Successor to [`2026-08-15-a-task-reaches-the-board-without-a-decision.md`](2026-08-15-a-task-reaches-the-board-without-a-decision.md),
which named per-declaration sign-off as the change worth making, set the criterion for
judging it, and designated its own run as the baseline. Sign-off landed on 2026-08-18 in
`c9ab068`. This record is the first measured comparison against that baseline, and the
issue set the comparison produces.

The headline is not the one the baseline record expected. Sign-off cost almost nothing.
What cost was a session dying with work in its hands.

---

## What is measured here, and what is inferred

Everything in the table below was computed from the session transcripts under
`~/.claude/projects/-home-wreel-Source-pptmstr/`, by summing `usage` records per session
and per subagent file. The decision and interception counts were taken by reading this
run's own transcript. Nothing in the table is recalled or estimated.

The attribution of the baseline to session `a35064e9` is an inference, and a checkable
one: it is the only session that declared nine tasks, its eight agents' final context
sizes sum to 942,522 against the baseline record's stated 932,640, and its span ends at
2026-08-16T11:13Z, which is the timestamp that record gives for its eighth agent's
post-crash report.

## The comparison

| | baseline `a35064e9` | this run `ff436d2c` |
|---|---|---|
| agents | 8 | 8 |
| **decisions offered before the spend** | **0** | **10** |
| **interceptions the operator had to invent** | **2** | **0** |
| tasks declared | 9, one unrun | 8 landed, 2 refused |
| sum-of-final-contexts | 942,522 | 865,914 |
| total output tokens | 1,068,054 | 579,183 |
| total billable including cache | 79.0M | 65.4M |
| main-loop turns | 149 | 131 |
| wall clock | 21.4h | 39.8h |
| longest single agent | 13.6h | 15.5h |

**The baseline record set this criterion itself**, and it is worth quoting because it is
the thing being tested rather than a metric chosen afterwards:

> The measurement that matters has changed with the framing: not tokens saved, but **how
> many decisions the operator was offered before the spend, and how many interceptions
> they had to invent instead.** On this run those numbers are zero and two.

Ten and zero. Both refusals redirected the run materially — the first ended a
photosensitivity line that was about to be boarded and turned it into comment-accuracy
work, the second ended the session's expansion. Neither required reaching into running
work. The baseline's two interceptions were an instruction shouted into a run already in
flight and a halt achieved by editing an inter-agent message in transit.

**The failure the baseline record feared did not happen.** Its main argument against
per-declaration sign-off was that the lead would do the work serially and the tool's
value would evaporate — a failure it noted would be silent. Output fell 46% and billable
17% on an identical agent count. Sign-off was close to free.

## The comparison is contaminated, and the contamination is the finding

This revision took two sessions. `8e875da7` ran first, produced a half-applied change in
the working tree and one task that was never claimed, and ended. `ff436d2c` inherited the
tree, reconstructed what had been intended from a transcript, and finished it.

Counting both against the baseline:

| | baseline | this revision, both sessions |
|---|---|---|
| total output tokens | 1,068,054 | 790,399 — **26% better** |
| total billable | 79.0M | 107.6M — **36% worse** |

The divergence is `8e875da7`'s 42.2M, spent for two agents and a tree nobody could read.
Cache reads dominate billable, and a restart means re-reading everything.

So the honest statement is not that gating made this cheaper. It is that **gating was a
rounding error and session mortality was the dominant cost line in the entire
comparison.** The most-examined link in the chain turned out to be the cheap one. The
unexamined link cost more than every approval decision, every fan-out and every re-read
in this run put together.

## The baseline's headline number is measuring the wrong quantity

`932,640` is a sum of eight agents' **final context sizes**. It double-counts the shared
prompt prefix, ignores everything before each agent's last turn, and is a peak-memory
figure carrying a cost label. That run's actual spend was 79.0M billable.

The two move differently and can move in opposite directions: sum-of-finals fell 8% this
run while output fell 46%. Anything optimised against sum-of-finals is being optimised
against how much context an agent happened to be holding when it stopped.

This matters because that record designates itself the baseline for future comparisons,
and its unbuilt meter — "Making the meter visible" — proposes surfacing the same figure
at declaration time. **Correcting the number in place is a separate act and has not been
done here.** It is proposed as issue 8.

---

## The issues

Each is stated as a structural change or it is not proposed. That constraint is the
baseline record's, and its reasoning holds: `CLAUDE.md` is monotonically pro-rigor, so a
paragraph asking for restraint is outvoted by construction, and `depends_on` prevented
two agents editing one file all day where "within reason" prevented nothing.

### 1. A declaration cannot say how its claims were established

`detail` is free prose, so a relayed inference and a measured fact are typographically
identical. On this run the lead wrote *"13 bands ... reorders the flattened pool, changes
every frame of the animation"* into a task as established. It was a builder's inference.
The builder that claimed the task inherited it as settled, rendered the frames anyway,
and found the flat pool byte-identical and 0 of 81 frames changed.

The baseline record already specified the field that would have caught this, under "What
a declaration must carry": *what was verified by running versus by reading, which this
repository already treats as the line between a result and a guess.* It was left unbuilt
as a schema change nothing had asked for. Something has now asked for it.

Cheapest high-value item in this set.

### 2. A finding has no disposal path, so it becomes a task or it evaporates

`worker_prompt` requires every worker to post a concern before finishing, which
structurally produces N findings for N workers. The lead then has exactly two verbs:
ignore, or `declare_task`. This run produced six concerns; four were load-bearing, two
became declarations that had to be refused as overreach.

The baseline record observed the same thing and treated it as one route onto an ungated
board. Under sign-off the pressure has moved rather than gone: the operator is now the
disposal path, and spends a decision doing it.

A third state — findings carried on the board as explicitly not-work — costs a field and
removes the pressure that turns every observation into a proposal.

### 3. `declare_task` can fail in a way that reads as a slow human

Two calls on this run returned *"PreToolUse hook did not respond before its timeout (host
client may be unreachable)."* The lead noticed and retried. A lead that did not notice
would proceed believing work was on the board.

The gate is now the link the whole design rests on, and its failure mode is currently
indistinguishable from an approval that has not come back yet. It needs to fail loudly
and to be distinguishable from a refusal.

### 4. A specification can exist where no reader outside one session can reach it

This run executed half its brief for most of its length. The missing half — *"the random
glitches on the quote line never seemed to have been implemented"* — was one sentence in
a premises file, and the specification it referred to lived in two places, neither
readable from here:

- as `Task.detail` on `8e875da7`'s board, which belongs to that session's team;
- as an operator message in that session's transcript, sent through the ordinary reply
  box, which never became `001-amendment.md`.

`brief.py` is append-only and `brief_pane.py` offers an amendment surface. Neither is on
the path an ordinary reply takes, so the brief directory is complete as an artifact and
incomplete as a brief. This is
[`archive/2026-08-15-an-operator-instruction-the-lead-cannot-see.md`](archive/2026-08-15-an-operator-instruction-the-lead-cannot-see.md)
surviving into two further channels.

It was recovered only because an agent was sent to read other sessions' transcripts.
That is not a mechanism, it is a rescue.

### 5. A session that dies holding work leaves no handoff

`8e875da7` ended with a half-applied change in the working tree, a task declared and
never claimed, and nothing written down about either. The successor session reconstructed
the intent from the diff and a transcript.

The board does not survive session death. No closing state is written. A task claimed by
a node that dies stays CLAIMED for the life of the session, which the baseline record
already recorded as an operator-surface gap — this is the same absence seen from the cost
side rather than the control side.

**On the numbers in this document, this is the expensive one.** It is the whole of the
36% billable regression, and it is the only line item in the comparison larger than the
thing the last cycle spent its effort on.

### 6. Register inheritance still has no counterweight

The baseline record diagnosed this and deliberately proposed no remedy, on the grounds
that any remedy would be briefing prose:

> The better the record, the more it costs to act on.

It fired again here, in the same shape. The lead read a rigorous archived record, matched
its register, and turned a docstring-accuracy job into a photosensitivity investigation
with a reviewer assigned to it. The operator's first refusal was the correction.

One structural angle the baseline record did not consider: a record could carry its own
tier as metadata rather than leaving a lead to infer it from prose quality. "Flavour UI"
versus "correctness defect" is data. A lead reading it would have something to calibrate
against that is not itself an argument.

Worth one design pass. Not a build, and not obviously right.

### 7. Agent duration has stopped being a cost signal

The baseline headlined *"individual agent durations up to 13.6h"*. This run's longest
agent ran 15.5h and spent most of it parked awaiting approval.

Under sign-off, duration measures time-to-human-answer. Reported undivided it will keep
being read as effort. It needs splitting into worked time and parked time.

### 8. Correct the baseline's metric in the baseline's own record

Per the section above: `932,640` is a sum of final context sizes, not spend. The
correction belongs in `2026-08-15-a-task-reaches-the-board-without-a-decision.md`, where
the number is quoted and where the comparison it anchors will be made again. The meter
that record proposes should surface the corrected quantity.

Convention question this raises and does not answer: that record is in `planning/` rather
than `planning/archive/` and is partly built, so whether a factual correction to it is an
edit or a dated note is not settled by the archive convention, which governs superseded
*reasoning* rather than wrong *arithmetic*.

---

## Ordering

Not a plan, and deliberately not a decomposition — the baseline record's argument that
work arriving with a remedy attached reads as already-decided applies to this document
too.

- **Cheap and clearly right:** 1, 3, 8
- **Cheap, and the pressure is real but has moved rather than grown:** 2
- **What the numbers actually indict:** 5, then 4
- **Design passes, not builds:** 6, 7

## Open

- **Whether the unit-of-sign-off question is still urgent.** The baseline record left it
  open and called it the question to answer first, on the grounds that per-task sign-off
  would have fired six times in five minutes. On this run the gate's own latency spread
  ten declarations across 40 hours and the burst never formed. That may mean the
  pathological case is self-limiting, or it may mean this run's shape hid it. One run does
  not distinguish those.
- **Whether continuity work belongs to the board or to the brief.** Issues 4 and 5 are the
  same defect seen twice — work exists in a place the next reader cannot reach. Whether
  the fix is one mechanism or two is not obvious, and answering it wrong produces two
  half-mechanisms.
- **What a handoff must carry.** Issue 5 says a dying session should write one; it does
  not say what. The risk is the same one the baseline record names about declarations: a
  handoff that costs what the work costs has moved the problem.
- **Whether any of this is measurable without a third run.** The decision and interception
  counts are discrete events and comparable. The token figures are aggregates over
  unequal work, and this comparison had a restart in it. A third run with no restart would
  separate the two, and there is no other way to get that.

## Not claimed

The two runs are not the same work. The baseline built the raster-line rework plus two
`driver.py` liveness fixes. This revision finished an inherited substitution-pool change,
built a quote-line animation the baseline never attempted, and wrote records. Comparable
in size, different in shape.

The figures that survive that objection are the decision and interception counts, because
they count discrete events rather than aggregating over unequal work. The token
comparison is offered as evidence, not as proof, and the 36% billable regression is
attributed to the restart on the strength of `8e875da7`'s own 42.2M rather than by
elimination.

Nothing in this document was built. No code was changed to produce it.
