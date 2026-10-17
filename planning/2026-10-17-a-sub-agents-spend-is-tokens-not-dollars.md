# A sub-agent's spend is tokens, not dollars

**Dated:** 2026-10-17 · **Status:** decided and built on branch `cost-display` ·
**Supersedes:** the "spend | measured: real data" row of
[`archive/2026-08-13-a-card-is-an-agent.md`](archive/2026-08-13-a-card-is-an-agent.md),
and the sentence under its "Measured" heading that "the card's spend slot is sound
the moment the §3 join is"

The August row was right about the data and wrong about what the card should draw.
The live probe (`scripts/verify_subagent_usage.py`) measured that a sub-agent's
assistant messages carry a **token** `usage`, and called that spend. The card was
then built to draw the **dollar** field of the same record. No message ever fills
that field for a sub-agent, so every worker's card read a measured-looking `$0.00`.

What reaches pptmstr, read from the installed SDK's types and the bundled CLI's
schema text, and not re-measured live
(`notes/2026-10-17-why-a-workers-card-reads-zero-dollars.md`):

- Tokens arrive per agent, on each assistant message, keyed through
  `parent_tool_use_id`.
- Dollars arrive once, as `ResultMessage.total_cost_usd`. The CLI's schema describes
  this as the whole query pipeline's cost, Task sub-agents included. No message
  carries dollars per agent.

So the row now reads:

| slot | status | evidence |
|---|---|---|
| spend, sub-agent | **tokens, measured per agent**. Dollars per agent do not exist on the wire | `UsageAccrued(node, …)` from `Translator._assistant`; `tests/test_cost_display.py` |
| spend, root | **dollars, the whole session's**. Drawn as `session $X` | `Translator._result`; the CLI's schema text, read |

Consequences that hold from here:

- No surface draws a dollar figure for a sub-agent. Details states it as not known,
  with the reason.
- Wherever the root's dollars appear, they are labelled as the session's. Tokens
  set beside them are the session's sum, derived on read
  (`session_details.session_usage`) and never stored.
- An estimate of a worker's dollars (tokens times a price table, or splitting
  `model_usage` by model) was not chosen. It would need a pinned price table and an
  "estimate of an estimate" label, and `run_total` would have to exclude it,
  because the root already counts it. Revisit only if the CLI starts reporting
  dollars per agent. `test_the_only_dollar_fields_the_sdk_parses_are_on_the_result`
  fails when the SDK grows such a field.

Still open, and not decided here:

- **The "session" label rests on schema text, not a run.** That `total_cost_usd`
  covers the workers was grepped from the bundled CLI 2.1.301 binary, and nobody
  checked that the CLI pptmstr launches is that build. The ui-rework tree's
  deferred probe list (`notes/2026-10-16-adversarial-probes-deferred.md`, item 6,
  not yet on this branch) names the one live run that settles it: spawn a worker,
  then compare `total_cost_usd` with the sum of `modelUsage[*].costUSD`. If the
  figure turns out to be the root's own, the label is wrong and the sub-agents'
  dollars are missing, not included. The tokens-on-workers half does not depend on
  the answer.
- **Resume.** A resumed session's first result already carries the earlier runs'
  total, according to the same schema text, so a resumed root's figure includes
  spend from runs this process did not see. Not measured, and not fixed here.
