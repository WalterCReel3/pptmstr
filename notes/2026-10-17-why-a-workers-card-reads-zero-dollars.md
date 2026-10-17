# Why a worker's card reads $0.00

Board task `cost-display-probe`. The operator asked whether cost tracking is a display
bug. **Verdict: (c), both, and mostly (b).** No message carries a sub-agent's dollars,
so nothing could put a real figure on a worker's card. That is the data gap. The
display bug is in how the gap is drawn: the card and Details show the missing value
as a measured `$0.00`, and Details calls it "estimated by the SDK". The root's figure
is not on the wrong card, but it is the whole session's cost and is drawn as if it
were the root's own.

Each claim below is marked **run** (a test in `tests/test_cost_display_probe.py`
executes it, 9/9 pass, each mutation-tested) or **read** (source or schema text read,
nothing executed). No CLI was started.

## 1. What cost data reaches pptmstr, per agent

| Message | What it carries | Per agent? | Basis |
|---|---|---|---|
| `AssistantMessage.usage` | a token dict for one message (`input_tokens`, `output_tokens`, cache counts). No cost field. | yes, through `parent_tool_use_id` | run (fields); the wire filling it for sub-agents was measured live in Aug (`planning/archive/2026-08-13-a-card-is-an-agent.md`, "Measured"), not re-run |
| `TaskProgressMessage` / `TaskNotificationMessage.usage` | `TaskUsage`: `total_tokens`, `tool_uses`, `duration_ms`. No dollars. | yes, by `tool_use_id` | run (`get_type_hints(TaskUsage)`); the CLI's own zod schema has the same three keys (read) |
| `ResultMessage.total_cost_usd` | one dollar figure | **no**: the whole query pipeline's | run (the field exists, no agent-keyed field does); meaning read, below |
| `ResultMessage.model_usage` | per-**model** tokens and `costUSD` | no, keyed by model | read |
| the CLI's `subagent_stats` on the result | spawn and outcome counts, no dollars | no | read in the CLI schema; **run**: the SDK parser drops it |

What `total_cost_usd` means. This was read from the schema descriptions in the
bundled CLI (`claude_agent_sdk/_bundled/claude`, VERSION 2.1.301, SDK 0.2.168),
using `grep -a` over the binary. Whether the `claude` that pptmstr launches is this
same build was not checked.

- It is "cumulative estimated cost in USD for this query() call, covering the same
  query-pipeline calls as modelUsage". `modelUsage` covers "main loop, **Task
  subagents**, sidechains, and internal calls such as compaction". So the root's
  dollar figure is **the session's cost, workers included**. It is not the root's own.
- It is "cumulative across turns … read the latest result rather than summing".
  `Translator._result` takes the difference from the last value it saw, which
  matches this.
- "A resumed or forked session continues from the total its transcript saved."
- "A mid-session /clear resets the running total."
- The CLI says outright: "Per-subagent detail is on the task_started /
  task_notification events". As shown above, those events carry tokens and no dollars.

**So no per-agent dollars arrive.** The only per-agent spend data is tokens.

## 2. What pptmstr does with it

Into the store:

- `Translator._assistant` sends `UsageAccrued(node, tokens)` to the node that
  `parent_tool_use_id` resolves to. Per-agent tokens are therefore accurate whenever
  the sub-agent's messages reach the stream. (run, `test_driver.py::test_subagent_usage_is_billed_to_the_subagent`
  and `test_lander_claims.py::test_dollars_reach_only_the_root_and_only_when_its_turn_ends`)
- `Translator._result` sends the change in `total_cost_usd` to `self.node_id`, which
  is the root. This is correct, because the figure belongs to the session. (run)
- `Translator._system` reads `task_progress` only for its description. The
  `total_tokens` in it is dropped. A worker whose assistant messages did not reach
  the stream would therefore show zero tokens as well as zero dollars. (run,
  `test_a_task_progress_token_count_is_read_for_its_description_only`)
- `UsageRollup.plus` in the `UsageAccrued` arm (`store._apply`). Nothing rolls a
  worker's usage up into its root. (read)

Out to the screen:

| Surface | Draws | Per-agent tokens? | Basis |
|---|---|---|---|
| rail card, every density (`rail._money(rec.usage.total_cost_usd)`) | each record's dollars. Workers show `$0.00`, the root shows the session total. | no | run (`test_a_workers_card_draws_zero_dollars_while_its_root_draws_the_session`) |
| Details, sub-agent (`session_details._spend_rows` via `_agent_view`) | `cost $0.00`, stated, with the note "estimated by the SDK, not billing". Beside it, the worker's real tokens. | **yes**, the only surface that shows them | run (`test_details_states_a_workers_zero_as_an_estimate_by_the_sdk`) |
| Details, session | the root's dollars and the root's own tokens | root only | read |
| HEALTH (`health.py`, root block) | `root.usage` dollars, which cover the session, beside `root.usage` tokens, which cover the root only | no | run at the store (`test_the_roots_tokens_are_its_own_and_its_dollars_are_the_results`); the draw itself was read |
| empty-queue line (`inbox.run_total`) | the sum of every record's dollars, which equals the sum of the roots' | no | run (`test_run_total_is_the_roots_figure_because_workers_add_nothing`) |
| status bar (`app._status_bar`) | no money, no tokens | — | read |
| session file (`fleetsync`, `Spent`) | the root's dollars | — | read |

## 3. Verdict

**(b) Data gap. Holds.** No message carries per-agent dollars, and the SDK parses
none (section 1). Putting a measured dollar figure on a worker's card is not
possible with this SDK and CLI.

**(a) Display bug. Holds, in a narrower form than the operator's premise.** Nothing
that exists is put on the wrong card. Moving the result's dollars onto a worker
would be wrong, because they are the session's. The defects are in how the data
is presented:

1. **`$0.00` asserts something false.** By STYLE.md's "A default where the honest
   answer is a refusal", a worker's `$0.00` is a default standing in for "not
   reported per agent". Details makes this worse: it says the SDK estimated the
   zero, and the SDK estimated nothing for that agent. Details' own `_unknown` row
   is the form it already uses for this case. (run)
2. **The root's figure is labelled as the root's but is the session's.** On the
   card and in HEALTH it reads as what the lead spent. HEALTH sets session-wide
   dollars beside root-only tokens, so the two figures on one line count different
   sets of calls. (session-wide: read; the tokens: run)
3. **Per-agent data that exists is not drawn where the dollars are.** Cards draw
   dollars, which are absent per agent, and not tokens, which are present. (read)

Both halves were already visible in the records. The August card design
(`planning/archive/2026-08-13-a-card-is-an-agent.md`, "spend | measured: real data")
measured that sub-agent *tokens* are on the wire, called that "spend", and the card
was then built to draw the *dollar* field of the same record. No message ever fills
that field for a sub-agent. That conflation is the cause. The renderer is not.

## 4. Fix sketches (none built)

Each sketch names the tests in `tests/test_cost_display_probe.py` that it is meant
to turn red.

- **Draw "—" (or "not reported") on a worker.** In `rail` and in `_spend_rows`, a
  record with `node_id[1] is not None` draws an unknown. In Details it is
  `_unknown("cost", "the CLI reports dollars per session, not per agent")`. This is
  the cheapest honest fix. It leaves `run_total` correct. It turns red:
  `test_a_workers_card_…`, `test_details_states_…`.
- **Draw tokens on a worker's card.** Draw tokens in the slot the dollars use now,
  for example `in+out`. Cache reads dominate counts and mean little, so drawing them
  needs a decision. This is measured data, and the August probe says it is filled
  on the wire. To cover a worker whose messages do not stream, also bank
  `task_progress.usage.total_tokens`. It is a running total, so it would be stored
  as a level, not added. That turns `test_a_task_progress_…` red.
- **Estimate dollars from tokens times price.** This needs a price table in
  pptmstr: a duplicated constant that must be pinned (STYLE.md §3), with input,
  output, cache-write and cache-read rates per model. It has to be labelled as an
  estimate of an estimate, and `run_total` must **not** add it, because the root
  already includes it. There is a tighter variant: `model_usage` gives exact
  `costUSD` per model, so when a worker's model (`Role.model`) differs from every
  other agent in the session, that model's cost is the worker's exactly. When
  models are shared it is only a proportional split. This one is not recommended
  first.
- **Roll workers up into the root.** On the dollar axis the CLI already does this.
  What is missing is the label: the root's card and HEALTH say "session $X", and
  HEALTH's token line sums the subtree so it counts the same calls as the dollars.
  This is a projection, computed on read and not stored (STYLE.md "Derive; do not
  store"). It changes `test_the_roots_tokens_…` only if the rollup is stored rather
  than derived.

The two fixes that do not conflict are "—" or tokens on workers, plus a "session"
label and subtree tokens on the root. Both stay within what the wire actually
carries.

## Side findings, not asked for

- **Resume.** The CLI's schema text says a resumed session's first result "already
  carries the earlier turns". `run()` builds a fresh `Translator` with
  `_last_cost = 0` (run: `test_a_fresh_translator_puts_the_whole_reported_total_on_the_root`).
  So a resumed root's card and `run_total` include spend from runs this process did
  not see, and `run_total`'s docstring says the opposite ("what this process has
  spent"). The same sentence settles the open question in the `fleetsync` module
  docstring ("whether a process started with `--resume` begins that total at zero")
  as **the second case**: the session file's sum does double-count across resumes.
  This was read, not measured live.
- **`/clear`.** `_result` latches the highest total it has seen, so after a reset,
  spend is not banked until the new total passes the old high (run:
  `test_a_running_total_that_falls_…`). Nothing in `pptmstr/` sends `/clear`, and
  `ConversationResetMessage` is handled nowhere, so this undercount is latent. (read)
- Whether the CLI forwards a **background** worker's assistant messages, and so its
  token usage, was measured once in August on an older CLI (2.1.226 era) and not
  since.

## What was not run

- No live CLI. Every statement about what `total_cost_usd` covers, about resume, and
  about `/clear` is the bundled CLI's schema text, read, not measured.
- No pane was drawn in a real ImGui context. The rail test uses `tests/test_rail.py`'s
  stub, and HEALTH was read, not drawn.
- No source in `pptmstr/` was changed. Mutations were injected as pytest plugins from
  `$TMPDIR`.

## What branch `cost-display` changed

Display only. The driver, the store and what either records are untouched, and so
is the resume double-count, which waits on a live measurement.

The "session" label below is built on section 1's reading of the schema text, which
has not been run. The ui-rework tree's deferred probe list
(`notes/2026-10-16-adversarial-probes-deferred.md`, item 6) holds the one live run
that settles it. If that run shows the figure is the root's own, the label is wrong.
The sub-agent half (tokens, and no dollars) does not depend on it.

The probe file is ported as `tests/test_cost_display.py`. The tests that pinned
the defect now pin the fix, and the rest stand as they were.

- **Rail cards, every density.** A sub-agent's card draws its own input and output
  tokens, for example `47.8k tok`, where it drew `$0.00`. Cache reads are left off
  the card because they dwarf the other counts; Details lists them. A root's card
  draws `session $1.87`. The ended density's spend slot is now measured rather
  than placed at a fixed offset, so the longer text fits. (`rail.card_spend`)
- **Details.** A sub-agent's `cost` row is "not known", with the reason "the CLI
  reports dollars per session, not per agent". Its `tokens` row is marked as its
  own. A root's `cost` row says it is the whole session's, sub-agents included.
  Its `tokens` row is the session's sum, and the note gives the root's own share.
- **HEALTH.** The dollar line is labelled as the session's. The token line under it
  is the session's sum, labelled "all agents", so the two count the same calls.
  The alternative was to keep the root's tokens and label them as the root's. The
  sum was chosen because HEALTH is the session's pane whichever agent is selected,
  and a reader comparing the two lines should be comparing like with like.
- The sum is `session_details.session_usage`, computed on read (STYLE.md "Derive;
  do not store"). It takes the dollars from the root alone, so a sub-agent record
  that ever carried dollars would not be counted twice. It still misses whatever
  never streamed: the CLI's internal calls such as compaction, and a worker whose
  messages were not forwarded. Both are in the dollars and not in the tokens.

Untouched, and why:

- **`inbox.run_total`** sums every record's dollars. That equals the sum of the
  roots' because a sub-agent's dollars are always zero, and the line already says
  it is this run's. The pin `test_run_total_is_the_roots_figure_because_workers_add_nothing`
  holds.
- **The status bar** draws no money.
- **The session file (`fleetsync`, `Spent`)** writes the root's dollars, which are
  the session's. HEALTH and Details already say so of a recalled session ("as the
  session file recorded … it may double-count across resumes").
- **A root before its first result** still draws `session $0.00`. Dollars reach
  the store only when a turn ends, and the record cannot tell "no result yet" from
  a measured zero. Telling them apart needs a field the store does not hold, and
  this branch changes no records. `tests/test_ui_text.py` already pins "$0.00" on a
  session that ran at zero cost.

Shots, before and after, of the fake team after a turn costing $1.87, made with
`scripts/screenshot.py --scenario spend`: `shots/cost-display/` in the ui-rework
tree.
