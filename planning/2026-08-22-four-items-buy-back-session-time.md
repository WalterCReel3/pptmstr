# Four items buy back session time; everything else waits

**Dated:** 2026-08-22 · **Status:** feature-team brief; scope is closed, ordering
is the priority ·
**Follows:** [`2026-08-22-an-approval-parked-overnight-is-not-a-wedged-host.md`](2026-08-22-an-approval-parked-overnight-is-not-a-wedged-host.md),
[`2026-08-21-a-lead-is-already-free-while-its-workers-run.md`](2026-08-21-a-lead-is-already-free-while-its-workers-run.md),
[`2026-08-21-a-dead-session-costs-more-than-its-approvals.md`](2026-08-21-a-dead-session-costs-more-than-its-approvals.md)

**Objective: maximize productive time per session improving the tool.** The
measured taxes on that time, in cost order: sessions dying silently while
holding work (18h in one measured instance), and an operator who cannot engage
a lead while its workers run (capability measured present, UI says otherwise).

This brief is written for an Opus 5 feature team, and its scope control is
structural, deliberately: `notes/2026-08-21-opus-5-and-work-nobody-asked-for.md`
establishes that no prose restraint instruction has controlled evidence behind
it, and that over-delivery, under-delivery and over-asking co-occur. So: each
item names its files — declare them as `touches` and the board enforces them;
each item has a done-when list — check every line before reporting done; and
the design decisions are pre-answered by the records above — read them instead
of stopping to ask. Work outside a named file list is not in this brief.

---

## Item 1 — A dead gate fails loudly (highest value per line)

**Why:** a PreToolUse hook timing out is indistinguishable from an operator who
has not answered yet. Session `7f0b40c2` burned three silent 6-hour cycles
against a host that never responded and stranded its deliverable. Dead-session
issue 3; measured cost ~18h.

**Scope (touches):** `pptmstr/driver.py` (the `_park` reject path and the
gate's timeout handling), `pptmstr/bridge.py` (a last-drain timestamp),
`pptmstr/app.py` (move the two watchdogs' logic off the thread whose death
they watch), matching tests in `tests/test_gate.py` / `tests/test_driver.py`.

**The build:**
- When a park ends by cancellation rather than decision, the resolution text
  must name the cause distinctly ("host did not drain for Ns" / "hook
  cancelled by CLI timeout") — the be4cc53 property: a refused call cannot be
  mistaken for the call's output, and a dead gate must not be mistaken for a
  patient one.
- A drain-stall watchdog on the asyncio loop: `Bridge.drain()` stamps; a
  loop-side task that sees parked approvals plus a stalled drain **warns
  loudly** (transcript ERROR segment, log). **Warn-only — it must not deny.**
  Its false-positive mode (OS suspend, closed lid) is exactly the weekend park
  the previous record protects. The deny arm waits on probe P2 and is not in
  this brief.

**Done when:** a test kills the frame loop with an approval parked and the
model-visible resolution text names the host, not the operator; a test proves
an OS-suspend-shaped stall (both clocks jump together) produces no deny;
`make check` green.

**Not in this item:** raising `APPROVAL_TIMEOUT_S` (probe-gated, deferred),
any persistence of parks, any UI beyond surfacing the warning.

## Item 2 — The UI stops denying the lead is free

**Why:** operator requirement, 2026-08-22. The driver forwards a mid-fan-out
prompt — measured, `scripts/verify_lead_turn_via_agent_session.py`,
ANSWERED-IN-WAIT-LOOP — but every surface says otherwise: `compose.py:84`
says "a message will be read after this turn" when it would be read now, and
`_needs_you` cannot list a waiting lead.

**Scope (touches):** `pptmstr/model.py` (a `SUPERVISING` member),
`pptmstr/driver.py` (emit it where `run()` enters `_await_subagents`; one
arm), `pptmstr/store.py` (`_needs_you`), `pptmstr/theme.py` (all three
per-state tables plus a Palette role — a missing entry is a KeyError inside a
draw call), `pptmstr/ui/compose.py`, `pptmstr/ui/rail.py` (`_claim` ends in
`assert_never`), `pptmstr/ui/health.py` (the AWAITING_INPUT-only hint line),
audit `pptmstr/ui/inbox.py` and `pptmstr/ui/widgets.py` for state-set reads;
tests: `test_store.py`, `test_inbox_rail.py`, `test_driver.py`,
`test_needs_you.py` all index `AgentState` heavily — update them, do not skip
them.

**Done when:** with a sub-agent live, compose offers input and says it will be
read now; `_needs_you` can surface the supervising lead; every per-state table
has the new member (grep `AgentState.` across `ui/` and `theme.py` and check
each site); `make check` green including the four named test files.

**Not in this item:** gate-task claimability / multi-stream board semantics
(separate, larger decision — deliberately out); any change to `_auto_depends`;
transcript-pane rendering work beyond what the state change itself requires.

## Item 3 — One unit test names the mixed-clock defect

**Why:** suspected (read, never run): `ApprovalNeeded.since` is epoch time,
`QuestionPending.since` is the monotonic frame clock, and consumers subtract
both from monotonic now — which would zero every approval's displayed age and
missort `_needs_you`. After any long park that display is the operator's
re-entry surface. If real, it bites at six hours today.

**Scope (touches):** one test in `tests/test_store.py` (or `test_needs_you.py`)
that constructs both obligation kinds and asserts age math and sort order
against a fixed clock pair. **If the test proves the defect, fix it in
`pptmstr/store.py`/`pptmstr/model.py` in the smallest change that makes the
test pass — same clock for both `since` fields — and no more.**

**Done when:** the test exists and is green; if it initially failed, the fix
is in and the test documents the constraint (which clock `since` uses), not
the history.

## Item 4 — Two probe scripts, one run each

**Why:** they gate the deferred timeout decision; per recorded practice the
probe is boarded and the fix is not.

**Scope (touches):** `scripts/verify_hook_timeout_ceiling.py` (P1: register a
PreToolUse `HookMatcher(timeout=604800)`, hook blocks 90s, assert the call
completes — rules out silent fallback to the 60s default) and
`scripts/verify_host_death_visibility.py` (P2: park an approval, kill the
host process, capture what the CLI does — decides whether the item-1 watchdog
ever needs a deny arm). Follow the conventions of
`scripts/verify_lead_turn_via_agent_session.py`: verdict from captured
ordering, never from narration; print-only; docstring states what one run can
and cannot settle.

**Done when:** both scripts exist, ruff/black/mypy clean, each run once with
the verdict line captured into this record as a dated addendum.

---

## Deferred, explicitly — do not pick these up

- Raising `APPROVAL_TIMEOUT_S` / splitting `SUBAGENT_CALL_VETO_S` (blocked on
  P1/P2 and the gate-parked-vs-in-flight question; previous record).
- Gate-task claimability for multiple streams (needs a task-kind on `Task`;
  schema + intent + bus + pane; separate decision).
- Park persistence across host restart (dead-session issue 5; operator's call).
- Anything in the record system itself (correction conventions, probe-backlog
  consolidation).

## House rules that bind this work

`STYLE.md` before writing; comments state live constraints, never history;
docstring triple quotes on their own line; black + mypy + the test suite are
part of the build (`make check`); commit messages are declarative sentences
arguing why, with **no attribution trailers of any kind**. The splash-test
failure `test_only_part_of_the_field_changes_on_any_one_step` (0.0692 > 0.06)
predates this work, lives in dirty splash files, and is not yours — do not
"fix" it and do not let it stop a green report on your own files. Declare
`touches` on every board task; a terminal task greens the gate after all
writes stop.

---

## Addendum, 2026-08-22 — Item 4 probe results

Environment for both: `claude-agent-sdk` 0.2.134, bundled CLI 2.1.226, Linux,
`claude-haiku-4-5-20251001`. One run each, verbatim verdict lines below.

**P1 — `scripts/verify_hook_timeout_ceiling.py`**

> NO SILENT FALLBACK: a 90.1s block under timeout=604800 completed,
> the shell echoed 'pptmstr-probe' back through a tool result, and the turn ended
> with terminal_reason='completed'.
> Any ceiling below 90s is ruled out at this magnitude. A clamp to some
> value above it is not -- this run cannot see one.

Bounds: a silent fallback to the 60s default is ruled out at `timeout=604800`.
A silent clamp to any value *above* 90s is not, and would look identical. The
7-day value being accepted is not the same claim as its being honoured for 7
days, and nothing here settles the second.

The completion signal is the echoed token coming back in a `ToolResultBlock`
correlated by `tool_use_id`, not the `ToolUseBlock` that requested it — the
request is the event PreToolUse fires on and appears whether or not the call
survives the gate, so a verdict resting on it would pass the exact run this
probe exists to catch. `scripts/verify_hook_timeout.py` computes its verdict
from the request (`observed["tool_ran"]`), which is worth knowing when reading
its §5.2.1 table; that does not change its recorded outcome, since its
`long-timeout` case also had `is_error` false.

Overlap noted: `scripts/verify_hook_timeout.py` case `long-timeout` (75s block,
`timeout=6h`, recorded in `orchestrator-design.md` §5.2.1) already established
the same property at 6h. P1 moves only the magnitude, to the one decision 1
needs.

**P2 — `scripts/verify_host_death_visibility.py`**

> SELF-DETECTS: every process the host started was gone 4.02s after
> the host was killed, having survived the same park for 20s while the
> host was alive. Host death, not the park, is what ends the CLI, and it does
> not leave a live one behind -- so item 1's drain-stall watchdog needs no deny
> arm to reclaim a session. Warn-only is sufficient for this failure.

The CLI wrote nothing to its inherited stderr on the way out. The 20s hold with
the host alive is the control: without it, "gone 4s after the kill" would not
be distinguishable from a CLI that ends every park at ~24s. Removing the kill
from the observer flips the verdict to `SURVIVES-ORPHANED` (mutation-checked,
not shipped), so the verdict discriminates rather than always reporting the
same thing.

**Bounds, and the case this does not cover.** P2 answers candidate 3 of the
previous record's ranking — *process fully dead*. It does not answer candidate
1's actual target. A **wedged** host (frame loop dead, process alive) keeps its
pipes open, so the CLI has nothing to notice and does not self-terminate; that
is precisely the state the drain-stall watchdog watches for, and P2 says
nothing about it. So P2 removes one reason a deny arm might be needed — orphan
reclamation — without establishing that no reason remains. Item 1's warn-only
scope is unaffected either way.
