# The inbox claims "oldest first" and sorts two different clocks

**Dated:** 2026-08-12 · **Status:** open, defect, not started ·
**Affects:** step 6 onwards — the inbox has been ordered wrong since it shipped

`_needs_you` (`store.py`) builds one list across all three obligation kinds and
returns `tuple(sorted(out, key=lambda o: o.since))`, documented as "oldest first,
because the operator works a backlog and the thing that has been blocked longest is
the thing costing the most."

It is not oldest first. `ApprovalNeeded.since` is `PendingApproval.requested_at`,
set from `time.time()` in `driver.py`. `QuestionPending.since` and
`SessionFailed.since` come from `state_since` / `ended_at`, which are
`time.monotonic()`. On Linux the two differ by about three orders of magnitude:

```
time.time()    1786566812.9
monotonic()       1147315.9
```

Sorting them together puts **every question and every failure ahead of every
approval, permanently**, on any machine whose uptime is less than the epoch — which
is all of them. Demonstrated against the real store: an approval parked first, a
turn ending a full hour later, and the hour-younger question sorts first.

```
needs_you, claimed 'oldest first':
  question  since=1150976.1     ended its turn - reply or close
  approval  since=1786566873.1  waiting the longest
```

## Why it matters more than a cosmetic sort

The list is the operator's work queue, and the ordering is the only thing telling
them what to do next. Worse, the two kinds are not equivalent: an approval blocks an
agent that is parked and burning nothing but wall-clock, while a question is a
session that has already stopped. Sorting the blocked one last inverts the priority
the projection exists to express.

It also quietly weakens the one guarantee `needs_you` was built for. That projection
replaced three lists that agreed by convention; having a single list whose *order*
is meaningless recreates the same class of defect one layer up.

## The fix, and the care it needs

One clock. `requested_at` becomes `time.monotonic()` like everything else in the
store, and `_needs_you` compares like with like.

Six call sites read `requested_at`, and they are the reason this was not folded into
step 8:

- `store.py` uses it as the fabricated placeholder's `started_at` when an approval
  arrives for an unknown node — same clock family, so it wants monotonic too;
- `driver.py` sets it;
- `fake_driver.py` sets it, also from `time.time()`;
- four test modules construct it directly with small float literals, which are
  already monotonic-shaped and would not change.

The thing to check rather than assume: **whether anything renders it as a wall-clock
instant** rather than as an age. Nothing appears to — the widgets format an elapsed
duration — but a monotonic value formatted as a date would be a 1970 timestamp, and
that is the failure mode worth grepping for before switching.

Worth a test that fails on the current behaviour first: two obligations of different
kinds, the older one an approval, asserting it sorts first. That test cannot pass
today by accident.

## Related, and deliberately separate

`AgentRecord.started_at` / `ended_at` / `state_since` are already monotonic and
consistent. This is only about `PendingApproval.requested_at` being the one field
that reached for a different clock, which is also why it survived review: in
isolation it is unremarkable, and it is only wrong in comparison to its neighbours.
