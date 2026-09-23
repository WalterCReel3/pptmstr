# What is left on the gate-policy branch

**Dated:** 2026-09-21, amended 2026-09-22 · **Status:** handover note, not a design
record · **Branch:** `experimental-gate-policy`, based on `85c95d1`

The design reasoning is in
[`2026-09-17-a-research-policy-reduces-the-orienting-burst.md`](2026-09-17-a-research-policy-reduces-the-orienting-burst.md).
This file is only what a next session needs to resume cold.

---

## Tree state

Green at 2026-09-21: `2039 passed, 6 skipped`, `make lint` and `make typecheck` clean.
The three macOS path failures that were red on `main` at `85c95d1` are fixed — they were
non-portable tests, not a defect in `brief.py`.

The suite has grown since, and the 09-22 session's terminal gate task is the reading to
trust rather than this line — a count written mid-session is stale by the next commit.

The 09-21 work was uncommitted and staged. It is committed now, along with the four
09-22 widenings below.

## What landed

- `pptmstr/shellscan.py` — pure read-only Bash classifier. `refusal(command) -> str | None`,
  `is_read_only(command) -> bool`. Allowlist-only `_Rule`; there is no field for a denied
  flag and `test_no_row_decides_a_flag_by_denying_it` forbids adding one, because
  deny-lists are defeated by `getopt_long` abbreviation. Segment support splits on
  `|`, `||`, `&&`, `;`, newline; bare `&` and `\r` stay refused.
- `approval.Policy` — rungs `STRICT` and `PERMISSIVE`, plus `classify(..., policy=)`.
  `PERMISSIVE` admits shellscan-passing `Bash` and nothing else.
- `driver.py` — policy per session, per-node scoping so sub-agents do not inherit.
- Launcher dial, `LaunchSpec.policy`, HEALTH gate display, status-bar count.
- `scripts/measure_bash_burst.py` — the acceptance test (see below).

## The two tasks that made it usable, and what came after them

Both landed. This section is what is now in the tree, because the version of it that
listed them as outstanding was the single most expensive thing in this file for a
session resuming cold.

### 1. `t-no-auto-revoke` — landed. The policy never changes by itself.

Operator decision, 2026-09-21, reversing `2026-08-11` §3. The reasoning is
record-consistent rather than an override: §3 rejects a time box and a call-count box
because *"both expire for reasons the operator cannot see"*, and an auto-revoke is that
same class of event. It applies §3's own criterion to the clause that escaped it.

`ACT_SHAPED` and `_NOT_ACT_SHAPED` are gone from `approval.py`, the dial writes in both
directions, and the finding that produced the reversal is worth keeping in front of the
next reader: **`shellscan` has no verdict meaning "this command acts"**. Every refusal
is a *can't vouch*, and its docstring rules out ever having one — which is why a refusal
could never have been a safe trigger for ending the phase. It was found while building
the mechanism it removed.

### 2. `t-widenings` — landed. `grep`, `sed` print-range and `cd` are in the table.

The table was shrunk correctly for danger and wrongly for value. `grep` is the second
most frequent command in the corpus and was deleted on reasoning alone.

`grep` is an unrestricted row, `sed` has `_refuse_sed` with a print-range grammar, and
`cd` has `_refuse_cd`. `rg` stayed out deliberately and the comment at `_TABLE` says
why: its hazard is a specific flag (`--pre` runs an arbitrary program), and under an
allowlist-only shape admitting it means enumerating every safe flag on the one row with
a literal "run this program" option.

One qualification the shorter version of this section lost, and it matters because the
operator's original complaint was about seds: `sed -n '1,$p' f` still parks, on
`shell metacharacter '$'`. `_SED_PRINT_RANGE` accepts `$` but the raw scan refuses the
character before dispatch reaches `_refuse_sed`, so that branch of the grammar is
unreachable. `sed -n 1,80p f` and `sed -n 1,2,3p f` admit.

### 3. The 2026-09-22 widenings — `echo`, quote-aware splitting, `2>/dev/null`

Four commits, all in `pptmstr/shellscan.py`, each with an adversarial corpus block in
`tests/test_shellscan.py`.

- **`echo` is a table row.** It reads nothing; it is there because a sequence is
  admitted only when every segment is and the model labels batched reads with one.
  Unrestricted, and it is a stronger claim than `grep`'s: past `-neE` the builtin has no
  option grammar at all and prints what it does not recognise, so a flag allowlist would
  park text.
- **`_split_segments` reads quoting the way bash does.** `grep -i "append|add_attr" f`
  used to be cut inside the quote and refused as unparseable. The metacharacter scan
  stays quote-blind, which is a separate decision and is why `grep -i "a&&b" f` still
  parks — on its `&`, from the scan.
- **`2>/dev/null` is stripped before the scan**, anchored on both sides, per segment.
  The anchors are the whole safety argument: without a right anchor,
  `2>/dev/nullcat /tmp/x` strips to `cat /tmp/x` while bash executes `/tmp/x`.
- **`scripts/verify_split_against_bash.py`** is new: a differential probe that runs
  candidate strings through `bash -c 'set -x'`, reads which commands bash actually
  executed, and requires a refusal whenever bash ran one the table refuses alone. It is
  the only verification artefact on this branch whose oracle is not a second reading of
  a manual. **Nothing runs it** — it is not in `make check`, which is the third instance
  of that pattern on this branch and is boarded separately.

## Deferred, with reasons

- **`t-bus-auto-reasons`** — make `_BUS_AUTO`'s per-member reasons load-bearing (a
  name-to-reason mapping with the set derived from its keys) so a member without a reason
  does not typecheck. Right idea; it is a mainline refactor riding on this branch. Three
  tasks have now been spent repairing that one comment, which is the tree saying the
  arrangement is wrong.
- **`t-pin-corpus-drift`** — pin the hand-written `Bash` verdicts in `test_approval.py` to
  `shellscan`. Keep the duplication; a derived corpus lets `shellscan` agree with itself.
  Do it once the table stops moving.
- **`t-rail-gate-display`** — TRIAGE shows *how many* sessions are widened, never *which*.
  Matters more than polish, because with nothing auto-revoking the display is one of only
  two bounds on the mode.
- **`t-record-slug-defect`** — a live mainline defect, unrelated to this branch:
  `brief.session_dir` transforms `/` but the CLI also transforms `.`, so on any path with
  a dot the briefs never sit beside the transcripts. Measured here: nine ground-truth
  `(cwd -> dirname)` pairs, `[^A-Za-z0-9] -> -` matches all nine, **but the sample contains
  no `_` or space so it cannot discriminate that rule from the narrower `/` and `.`**.
  Migration is the harder half — briefs already exist under the wrong slug.

## The numbers, and what they are worth

`scripts/measure_bash_burst.py`, run 2026-09-22 after every widening above landed and
after the script itself was repaired — it had been measuring with the pre-quote-aware
split while claiming to use the classifier's own. **Exit status 0**, self check 14/14,
monotonicity clean. 149 transcript files, 85 with a Bash call, 2755 Bash calls.

| | 2026-09-21 | 2026-09-22 |
|---|---|---|
| first 3 calls of a session | 27.1% | 53.8% |
| first 10 | 11.6% | 49.3% |
| overall, per session | 4.7% | 38.1% |
| overall, all calls including sub-agents | — | 41.1% |

**The two columns are not a clean delta**, and both differences flatter the right-hand
one. The 09-21 figures were candidate simulations of widenings that had not landed, and
the first-N rows counted 46 sub-agent transcripts as sessions — the script treated one
file as one session until it was repaired on 09-22. Read the direction and the
magnitude. The 09-17 record's 09-22 amendment has the per-widening breakdown, which is
the honest version of "what did each change buy".

**Two overall figures, and they answer different questions.** 41.1% (1133/2755) is what
fraction of `Bash` calls the gate admits. 38.1% (586/1538) is what fraction of an
*operator's session* it admits, over the 39 session transcripts with sub-agent slices
excluded. The gap is not noise: 46 of the 85 files are sub-agent slices, they are 1217
of the calls, and they admit at 81.2% on their own first three against a session's
53.8%. A sub-agent file is not a session — it opens mid-task against a brief somebody
else wrote. Much of that traffic is this branch's own agents probing this branch's own
gate, so the corpus is measuring the experiment along with the subject, in the
flattering direction. **Quote 38.1% when the claim is about what an operator
experiences.**

**Sequence support is still most of it.** The counterfactual arm with segment support
withheld scores 11.3%, against the tree's 41.1%. Every widening the script can simulate
has now landed, so its candidate arms C through F are arm B under other names and the
table above has no "with the next task" column left to fill.

**The ceiling is real.** The largest still-refused groups are `cd` (410), `source`
(198), `.venv/bin/python` (150), `git` (148), `make` (101) — and 506 of 1622 refusals
have a head segment the table admits and fail on a *later* segment. The residual is
`source`, `make`, `python`, `gh` and composites where one segment acts. None of it is
parser-fixable.

**Why it cannot go further.** Deciding "does this command write" is undecidable — it
reduces to halting. `shellscan` sidesteps that by deciding a *syntactic* membership
question with a fail-closed default, which is why it terminates and why it is safe. The
ceiling is the price of that decidability. The sandbox work changes the question from
"can I decide what this command does" to "can I bound what any command can do", which is
the only thing that moves the number materially.

**Caveat on every figure here.** The corpus is `~/.claude/projects`, which the measuring
session is concurrently writing to — the instrument is inside the thing it measures. Three
independent runs agreed within 0.4 points, so no decision turns on it, but these are dated
snapshots, not constants.

## What the safety story now is

Two things, and only two: the allowlist, and the operator seeing the dial is on. Writes,
spawns and messages park individually, so nothing *acts* unattended. But with no
auto-revoke a `shellscan` false negative can fire at any point in an arbitrarily long
session rather than inside a short window. That trade was made deliberately. It is why
`2026-08-22` D3 — *"displaying the dial is part of shipping the dial"* — is load-bearing
rather than presentational, and why the TRIAGE gap above is worth closing.

`WebFetch`/`WebSearch` are denied under `PERMISSIVE` because an admitted `cat` of any
absolute path pairs with an auto-approved fetch into an unattended read-then-send with no
approval record for either half. Re-adding them by name fails six tests across three
guards.

## Process lessons, recorded because they cost real work

- **Measure before building.** The transcripts were on disk from the first minute. Running
  the measurement first would have prevented dropping `grep`, would have shaped the table
  differently, and would have shown the auto-revoke ceiling before two tasks were spent on
  it. Most of this branch's churn traces to that one omission.
- **Decisions sent as board notes race task completions.** Two rulings arrived after the
  task they governed had completed. A decision belongs in a declared task, not a message.
- **Notes that expand scope bypass the board's `touches` collision detection.** A note can
  pull a file into a task's work while the `writes` line still does not mention it, so the
  overlap is invisible to anyone reading the board. That produced one near-miss.
- **Simulate by extending the real classifier, never by short-circuiting around it.** Two
  measurement bugs, both from shortcuts that skipped the real table; one produced an
  impossible result that was only caught because it was impossible.
