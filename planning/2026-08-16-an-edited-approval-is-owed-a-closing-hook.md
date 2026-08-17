# An edited approval is owed a closing hook nobody has measured

**Recorded 2026-08-16**, from two sub-agent cards left spinning after their agents had
finished. Not fixed, and not yet diagnosed past the branch it lives on.

---

## What was seen

Three sub-agents ran to completion and reported. Two kept a live `thinking` badge in
the rail long afterwards. The session log carried **no** `sub-agent ... settled:` line
for either, which is what makes this diagnosable rather than a guess.

That absence rules out both settle paths. `_settle_silent_subagents`
(`driver.py:1643`) would have fired at `SUBAGENT_SILENCE_S` = 300s and reported
FAILED; `_subagent_stop` (`driver.py:907-941`) would have reported DONE and popped
`_subagent_in_flight` outright. Neither ran. The only remaining state is
`_has_call_in_flight` returning True at `driver.py:1666` and skipping the settle —
an unclosed bracket, held for `SUBAGENT_CALL_VETO_S` = `APPROVAL_TIMEOUT_S` = six hours.

## Which two

The two stuck agents were exactly the two whose `post_concern` the operator
**annotated** before delivery. The third agent's concern went through unedited and
its card settled normally. Three for three, which is a correlation on n=3 and is
offered as the reason to measure the branch, not as the finding itself.

## The branch

An annotated approval sets `decision.edited_args`, and the gate returns an allow
carrying `updatedInput` (`driver.py:202`, `driver.py:1260`). `_is_allow`
(`driver.py:206-221`) reads only `permissionDecision == "allow"`, so an edited
approval is an allow — correctly, since the tool does run. The consequence is that
`_pre_tool_use`'s `finally` at `driver.py:1004` declines to close the bracket and
leaves a PostToolUse owed.

`scripts/verify_post_tool_use.py:84` returns only plain `"allow"` or `"deny"`. It
never sets `updatedInput`. So edit-then-approve is the one gate path with no
measurement behind it, and the docstring at `driver.py:1016` that cites the script is
claiming coverage the script does not have.

## What is not known

Why the closing hook goes missing on that path. Three candidates, and they take three
different fixes:

- the CLI emits no closing hook at all for a call whose input was rewritten;
- it emits one without `agent_id`, which `_post_tool_use` drops at `driver.py:1023`;
- it emits one under a different `tool_use_id`, so `_end_tool_call` matches nothing.

## The measurement

A third case in `scripts/verify_post_tool_use.py`: return `allow` **plus**
`updatedInput` from the probe's `pre_tool_use`, then record whether a closing hook
arrives and what `agent_id` and `tool_use_id` it carries. That distinguishes all three
candidates in one run and needs no change to `driver.py`.

## Cost while it stands

Each stuck entry holds a slot in `_live_subagents` for six hours, and capacity is read
off that set (`driver.py:1681`). Two of `DEFAULT_SUBAGENT_CAP` = 4 sub-agent slots
were unavailable. Both the log and `_subagent_in_flight` are process state, so a
restart clears it — which also means the evidence does not survive one.

## Related

The veto exists because of [2026-08-14-a-halt-has-to-reach-work-that-has-not-started-yet]
and the liveness work that followed it. Nothing here argues the veto is wrong: a call
genuinely in flight must not be settled on a clock. What is wrong is that one way of
allowing a call is not on the list of ways a call can end.
