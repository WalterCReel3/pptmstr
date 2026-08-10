# Dogfooding notes

**Dated:** 2026-08-09 · **Status:** open · **Follows:** the §8 build order being complete

All seven build-order steps are done. The next backlog comes from using the thing
rather than from guessing, so this file is where friction gets written down as it
happens instead of being remembered later.

## Driving it today

```sh
make run                                   # empty window, nothing to do (see below)
.venv/bin/python -m pptmstr --task "..."   # the real entry point
.venv/bin/python -m pptmstr --task a --task b --cap 2
.venv/bin/python -m pptmstr --fake         # UI only, no SDK, no cost
```

`j`/`k` move, `a` approve, `r` reject with a reason, `e` edit arguments then
approve, `Shift+A` approve all from the selected agent.

## Known sharp edges — already understood, no need to rediscover

These are gaps I know about. Report them only if the *severity* surprises you;
that judgement is the useful signal, not the existence.

- **No way to start an agent from the UI.** `--task` at launch is the only route,
  so adding work means restarting. `make run` with no arguments gives an empty
  window with nothing to do.
- **No way to stop one.** `SessionPool.interrupt()` exists and nothing calls it.
  A misbehaving agent can only be stopped by killing the window, which takes its
  siblings with it. This is the one I would call a safety gap rather than a
  missing feature — mitigate with a low `--cap` and by reading diffs before
  approving.
- **No trust promotion.** §5.4 lists per-node "trust this tool class" as day-one
  batching; it was scoped out of step 4 and never came back. Long sessions
  re-approve the same class of call repeatedly.
- **No fork/retire.** §2.4 makes "fork this session" the offered action when a
  session compacts. The ring will tell you to retire a session and give you no way
  to do it.
- **Cost is collected and never shown.** `UsageRollup` accumulates on every
  message; no widget reads it. `max_budget_usd` is unwired, so there is no hard
  stop on spend.
- **Sub-agent output is not live.** Confirmed upstream behaviour, not a bug here
  (§2.5.1). The pane says so on sub-agent rows.

## Open questions the design still carries

- Keystroke-to-frame latency is reasoned, not measured — no injection tooling on
  the build box. It matters most when typing a rejection reason with everything
  parked, so it is worth a subjective verdict from real use even without numbers.
- Whether `ResultMessage.total_cost_usd` is per-turn or cumulative. The driver
  deltas against the last value, which is correct either way, but the ambiguity is
  unresolved and would show up as visibly wrong cost once cost is displayed.

## Friction log

Append as it happens. Raw is fine — the point is to capture it before it gets
rationalised into a feature request.

| date | what happened | what it cost | guess at the fix |
|---|---|---|---|
| | | | |

## Debt to clear regardless of what dogfooding says

- `pptmstr/fake_driver.py` was meant to be deleted when step 3 landed and has been
  grown instead. It is now load-bearing for screenshots and benchmarks, which is
  exactly the outcome the original note warned against. Either delete it or
  promote it deliberately to a test fixture and say so.
- No CI. `make check` is the gate and nothing runs it automatically.
- The sub-agent `tool_use_id` ↔ `agent_id` join is by adjacency and can mis-pair
  under parallel spawns from one parent (§2.5.1). Enrichment only, so the cost is
  a topic under the wrong sibling — but it is unverified under actual parallelism.
