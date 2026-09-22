# What is left on the gate-policy branch

**Dated:** 2026-09-21 · **Status:** handover note, not a design record · **Branch:**
`experimental-gate-policy`, based on `85c95d1`, **nothing committed**

The design reasoning is in
[`2026-09-17-a-research-policy-reduces-the-orienting-burst.md`](2026-09-17-a-research-policy-reduces-the-orienting-burst.md).
This file is only what a next session needs to resume cold.

---

## Tree state

Green, two readings: `2039 passed, 6 skipped`. `make lint` and `make typecheck` clean.
The three macOS path failures that were red on `main` at `85c95d1` are fixed — they were
non-portable tests, not a defect in `brief.py`.

Everything is uncommitted; most of it is staged. `tests/test_gate.py` is `MM` (staged
and further modified).

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

## The two tasks that make it usable

Both fully specified, no open questions. Together they take the admit rate from 4.7% to
18.8%, and from 27.1% to 47.1% across the first three Bash calls of a session.

### 1. `t-no-auto-revoke` — the policy must never change by itself

Operator decision, 2026-09-21, reversing `2026-08-11` §3. The reasoning is
record-consistent rather than an override: §3 rejects a time box and a call-count box
because *"both expire for reasons the operator cannot see"*, and an auto-revoke is that
same class of event. It applies §3's own criterion to the clause that escaped it.

- Remove the revoke at the `classify` site in `driver.py`, and `ACT_SHAPED` /
  `_NOT_ACT_SHAPED` in `approval.py` if nothing else uses them (grep first).
- **Add the widening write.** The dial currently only narrows, so a session that drops to
  `STRICT` can never be relaxed again — a trapdoor wearing a control's clothes.
- Rewrite the monotonicity/thread-safety comment in `driver.py`; its premise ("the only
  transition is RESEARCH -> STRICT") is false in both directions after this. The shape
  afterwards is one writer (UI thread, both directions) and one reader (asyncio thread at
  `classify`), which is what `2026-08-11` §5's atomic flag was designed for.
- Fix `revoke_policy`'s docstring — it still describes the phase ending at "the first call
  the phase would not admit", which has been false through two changes.
- Two now-false worked examples cite `git log --oneline | head` as refused for its pipe,
  in `driver.py` and `approval.py`. The arguments survive; only the examples died.
  `tail -f build.log` replaces them.
- Keep: `_policy_for` and sub-agent non-inheritance; the at-cap spawn deny ahead of
  `classify`; and the finding that **`shellscan` has no verdict meaning "this command
  acts"** — every refusal is a *can't vouch*, and its docstring rules out ever having one.
  That is the strongest argument for the reversal and it was found while building the
  mechanism being removed.

### 2. `t-widenings` — re-admit `grep`, `sed` print-range, `cd`

The table was shrunk correctly for danger and wrongly for value. `grep` is 459 calls as
head in the real corpus and was deleted on reasoning alone.

- `grep` as an **unrestricted** row. A reviewer went through GNU and BSD option sets and
  found no write or exec primitive; verify before relying on it, since the same method
  missed `file -C` and `tree -o`.
- `rg` is **deferred, deliberately** — its hazard is specific flags (`--pre` runs an
  arbitrary program), and under the allowlist-only shape admitting it means enumerating
  every safe flag on the one row with a literal "run this program" option. 8 calls in the
  corpus against grep's 459. Say in a comment that the absence is a decision.
- `sed` **print-range only**, as an allowlist grammar: digits, commas, `$`, trailing `p`,
  nothing else. `-n` permitted; `-e`, `-f`, `-i` and any `-i`-clustered form refused.
  `sed` was dropped because its write primitive (`w`, `s///w`) is in the *program text*
  where no flag rule can see it, and GNU `sed` is Turing-complete. **If the grammar
  cannot be made obviously correct on reading, ship without `sed` and say so** — that is
  4.7 points worth losing rather than handing back.
- `cd`, refusing a bare `cd` and any argument containing a substitution.
- Every command string in the reviewer's concerns must still be refused afterwards.
- Re-run `scripts/measure_bash_burst.py` and report the rate.

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

`scripts/measure_bash_burst.py`, over ~2400 real Bash calls from the operator's own
transcripts. It carries its own self-check and a monotonicity gate wired to exit status.

| | today | with the two tasks |
|---|---|---|
| first 3 calls of a session | 27.1% | 47.1% |
| first 10 | 11.6% | 29.6% |
| overall | 4.7% | 18.8% |

**The ceiling is real.** Under the widest candidate the largest still-refused groups are
`cd` (483), `grep` (319), `source` (182), `git` (173) — and 38% of refusals have a head
segment the table admits and fail on a *later* segment. The residual is `source`, `make`,
`python`, `gh` and composites where one segment acts. None of it is parser-fixable.

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
