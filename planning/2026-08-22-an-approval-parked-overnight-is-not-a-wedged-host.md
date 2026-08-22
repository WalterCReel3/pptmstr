# An approval parked overnight is not a wedged host

**Dated:** 2026-08-22 · **Status:** two operator decisions recorded; probes named;
nothing built ·
**Follows:** [`2026-08-21-a-dead-session-costs-more-than-its-approvals.md`](2026-08-21-a-dead-session-costs-more-than-its-approvals.md),
[`2026-08-21-a-lead-is-already-free-while-its-workers-run.md`](2026-08-21-a-lead-is-already-free-while-its-workers-run.md)

Two operator decisions, stated 2026-08-22, recorded here with the analysis that
has to constrain their build.

**Decision 1: a parked approval survives the operator's absence — overnight, and
over a weekend.** `APPROVAL_TIMEOUT_S = 6h` (`driver.py:90`) was set as "a
backstop against a wedged UI, not a review deadline... far beyond any plausible
human latency." The operator's stated usage falsifies that premise: leaving an
interactive session parked for 60–72h is normal use, not an anomaly the backstop
may eat.

**Decision 2: composing a prompt to a lead whose workers are running is required
UI work, not optional polish.** The driver side is measured closed:
`scripts/verify_lead_turn_via_agent_session.py` (one run, 2026-08-22,
ANSWERED-IN-WAIT-LOOP — canary at 7.47s against a sub-agent stop at 38.12s)
showed `_await_subagents` forwards a mid-fan-out prompt through the full real
stack. What remains is everything that currently tells the operator otherwise:
the `SUPERVISING` state and its surfaces (`compose.py`'s "will be read after
this turn", `_needs_you`, `rail.py`, `theme.py`'s three per-state tables,
`health.py`'s hint line), sized in this session at roughly six mechanical files
plus the `AgentState` test surface. Gate-task claimability for multiple
concurrent streams is a separate, larger decision and is deliberately not
folded in here.

---

## Why decision 1 is two mechanisms, not one constant

The constant serves two masters. As a review deadline it is too short — that is
decision 1. As a backstop against a dead or wedged host it is too *slow* and
too quiet: session `7f0b40c2` spent three consecutive, silent, exact 6-hour
hook aborts against a host that never answered, burned ~18h of wall clock, and
stranded its deliverable in context (recovered 2026-08-22; see the lead-free
record's provenance section). The burn scales linearly with the constant:
raising 6h to 72h without a separate host-death mechanism turns the identical
failure into ~9 days. The split is forced by arithmetic, not preference:

- **Operator latency:** unbounded, or weekend-scale. An away operator is
  definitionally alive-and-will-return; the gate's park is the correct state.
- **Host death:** detected fast and failed *loudly* — a deny whose reason names
  the host, distinguishable from a refusal (the be4cc53 property), so the model
  stops retrying instead of cycling the timeout.

## What is known, what is inferred

Read-derived map, this session (citations in the session's board record):

- Enforcement is CLI-side, inside the bundled binary. The SDK documents no
  maximum for `HookMatcher.timeout` and enforces nothing itself; whether the
  binary accepts 259200–604800s is **unproven** (probe P1). On expiry the CLI
  synthesizes an `is_error` tool result and the session survives and may retry
  — the 18h loss was three clean cycles, not a crash.
- **The veto is coupled:** `SUBAGENT_CALL_VETO_S = APPROVAL_TIMEOUT_S`
  (`driver.py:132`) because a shorter veto lets `SUBAGENT_SILENCE_S` falsely
  fail a sub-agent legitimately parked at the gate. Raising the timeout raises
  the veto, and a lost closing hook then holds a capacity slot for the whole
  weekend. *Inferred, not settled:* whether gate-parked (approval pending) and
  post-approval in-flight are mechanically distinguishable, which would let the
  veto stay bounded where the risk lives. This is the first design question
  the build must answer.
- **Cache economics do not argue against long parks.** The cache tier observed
  in `7f0b40c2` is 1-hour ephemeral: any park past ~1h already forfeits it, so
  60h costs what 6h costs — ~90k cache-write tokens per parked node on resume
  (run-derived from that transcript's usage blocks), against the ~42.2M-token
  restart the park avoids. Parking is roughly 500× cheaper than dying.
  Comparability of those two figures is read from two different runs and is
  flagged, not certified.
- Related records that scale with the constant: the 08-16 closing-hook cost
  table ("the same six hours" throughout), and the 08-14 halt record's
  degradation path — a halt relying on timeout-degradation at 72h takes 72h.

## Host-death detection, ranked — and the trap

Both existing watchdogs run on the frame loop, the thread whose death is the
failure; they die with the thing they watch. Candidates, cheapest first:

1. **Drain-stall watchdog on the asyncio loop** — stamp `Bridge.drain()`, and
   a loop-side task treats "approvals parked + drain stalled" as host-wedge.
   **Warn-only until probe P2 decides the deny.** Its false-positive mode is
   exactly decision 1's scenario: an OS suspend or closed lid stalls the same
   clocks and drains it watches, and a false positive auto-denies the parked
   approvals the mechanism exists to protect. A deny needs either a
   suspend-aware clock or the pipe evidence below.
2. The same watchdog over `Bridge.ask` futures (the mode-dial record's
   silent-refused-control class rides the same fix).
3. **Process fully dead:** only the CLI survives to notice. Whether it treats
   closed pipes as abort-now is unreadable (binary) — probe P2. If it does,
   pipe-close is the discriminator and the watchdog above never needs to deny;
   if it does not, an external supervisor (heartbeat file, stale-session
   killer) is the loud fallback.

## Flagged on the way, not settled here

- **Mixed clocks in obligation age** (suspected defect, three reads never run
  together): `ApprovalNeeded.since` is epoch time, `QuestionPending.since` is
  the monotonic frame clock, and both consumers subtract from monotonic now —
  which would clamp every approval's age display to zero and missort
  `_needs_you`. After a weekend park that display is the operator's re-entry
  surface. One unit test names it; if real it bites at 6h today.
- **Park persistence.** A parked approval survives the *operator's* absence
  only if the host process survives it too. Whether decision 1 implies the
  park surviving a host restart is dead-session issue 5 territory and is left
  as the operator's call — nothing here assumes it.

## Probes, before any constant moves

- **P1:** `timeout=604800` on a hook that blocks 90s; assert completion.
  Rules out a silent fallback to the 60s default; does not prove no cap below
  72h.
- **P2 (the discriminator):** kill the host mid-park; watch the CLI. Decides
  whether host-fully-dead self-detects, i.e. whether the watchdog ever needs
  its deny arm.
- **P3:** `async_` hook output on PreToolUse — defer, run, or hang.
- **P4 (free):** instrument the first real weekend park. The operator's stated
  usage is itself the experiment.

## Verification boundary

**Executed this session:** `scripts/verify_lead_turn_via_agent_session.py`,
once, timeline in the script's session record; ruff, black, mypy on that
script.

**Read, not run:** everything else above — the SDK source, the transcript
usage blocks the cache figures come from, and every `driver.py`/`bridge.py`
citation.
