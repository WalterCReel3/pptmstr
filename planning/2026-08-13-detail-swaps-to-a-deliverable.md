# DETAIL swaps to a deliverable, and the swap is the signal

**Dated:** 2026-08-13 · **Status:** proposed, not built · **Supersedes:** step 4
of 2026-08-11-what-it-said-is-a-byte-tail · **Blocked on:**
2026-08-11-turn-end-still-marks-done

Line numbers in this doc are given only where a symbol name would be ambiguous.
The 08-11 docs' citations have drifted 30–130 lines against the working tree in
under two days, and a stale number is worse than no number because it reads as
verified.

## The rule this settles

The 08-11 rule stands — the inbox row is where you act, DETAIL is what informs
the act. Dogfooding added *when*:

> **DETAIL populates on obligation. The population is itself the signal.**

Streaming and that rule are opposed. A surface that updates every frame carries
near-zero information per frame; the operator habituates and stops checking it.
A surface that goes from quiet to full in one frame is a step function, and step
functions are what peripheral vision is built to detect. Narration was not merely
unnecessary. It was spending the signal.

This corrects the reasoning of step 4, not its measurement. That step priced the
retained cursor honestly — a per-frame parse is unaffordable, a per-frame read is
not — and concluded wrap-and-pin because "rich rendering is for prose that has
stopped moving." The measurement holds. The conclusion does not follow, because
the question was never what is affordable to draw. It is what an operator
notices from across a desk while doing something else.

## The two states

DETAIL is per card, and a card has exactly two states:

**Working.** Mid-turn comments append as fully rendered paragraph steps. Each
comment is its own render unit — a `BlockCursor` opened and `finish()`ed per
comment, memoised individually. This is `DetailState.prose_blocks` generalised
from one memo per node to one per boundary; it is a change of key, not of
mechanism.

**Obligation.** The deliverable *replaces* the comment list and renders whole.

The swap is the event. Appending the deliverable below accumulated comments would
give the weakest possible version of the signal — a small addition at the bottom
of an already-full pane — and it is also semantically wrong: comments are
progress, the deliverable is a demand, and once the demand arrives the progress is
history.

Prior deliverables collapse to a one-line header at the top when the next turn
begins. That is where session accumulation lives. Without it the only record of a
finished answer is the raw stream, and a finished answer is the artifact most
worth keeping — sending the operator to CONTEXT to find one is the demotion in
the 08-11 doc applied to exactly the case it should not cover.

Comments are ephemeral in DETAIL and durable in CONTEXT. Nothing is lost on the
swap; it moves to the surface you would go looking on anyway.

CONTEXT is untouched. Bytes stream as they do now.

## Why boundary markers, and not a new `SegmentKind`

`blocks.py:327` markdown-parses `SegmentKind.OUTPUT` and renders every other kind
as `LITERAL`. Comments must be fully rendered, so comment bytes must stay
`OUTPUT`. The comment/deliverable split therefore lives in **message boundaries
recorded beside the segment list**, not in the kind. The requirement forces the
representation; this is not a workaround for one.

Three independent reasons the kind-tagged version could not have been built
anyway, recorded so it is not re-proposed:

- `AgentSession` sets `include_partial_messages=True` unconditionally, so
  `Translator._stream` has already appended the prose as `OUTPUT` before
  `_assistant` sees the complete message — where `already_streamed` skips the
  text block. A kind decided in `_assistant` has nothing left to write.
- Writing it from both paths is the defect `already_streamed` exists to prevent,
  the one whose comment records "a one-character answer came back as `99`", pinned
  by `tests/test_driver.py`.
- Relabelling after the fact is not available: `Transcript`'s writer side is
  `append` / `close_segment` with no relabel operation, and a kind change
  force-closes the open block, which would revise committed blocks against
  `BlockCursor`'s never-revised invariant — the invariant CONTEXT's RICH mode
  depends on.

## Staging is a disposition switch, not a done flag

`stop_reason` decides where the accumulated bytes go, per value:

| value | disposition |
| --- | --- |
| `end_turn` | deliverable — swap |
| `tool_use` | comment — append |
| `max_tokens`, `pause_turn` | fragment — hold, concatenate with the continuation |
| `refusal`, `stop_sequence` | undecided |

One switch, and comment and deliverable come out of the same mechanism rather
than needing two. Rendering a truncated fragment as a finished paragraph step
would be a visible lie, which is why the fragment row is not folded into either
of the first two.

**This is contingent and must be verified before it is built.**
`AssistantMessage.stop_reason` exists in the installed SDK
(`claude_agent_sdk/types.py:1038`, populated in `_internal/message_parser.py`)
and is read nowhere in pptmstr. No value from this CLI build has been observed —
the field is `str | None` and defaults to `None`. If it arrives `None` the whole
staging model needs another discriminator, and the obvious fallback is wrong:
"an assistant message carrying no `ToolUseBlock` is the turn's last" fails for
`max_tokens` and `pause_turn`, both of which produce text-bearing, tool-free
messages mid-turn. Under that fallback each one renders a false "done".

`stop_reason` is per message, which is why it is preferred over
`ResultMessage.result` even if that field turns out to carry the final text: a
sub-agent emits no `ResultMessage` for its own node — `Translator._result` passes
`self.node_id` on every path — so nothing `ResultMessage`-based can ever serve a
card that is a sub-agent.

## The blocker

`AgentFinished(DONE)` fires on every ordinary `ResultMessage`; `run()` reaches
`AWAITING_INPUT` only afterwards, past `_await_subagents` (up to
`SUBAGENT_GRACE_S` = 120s) and `_poll_context()`.

In that window the card is no longer active, so the comment list tears down, and
no obligation exists yet, so nothing swaps in. Under narration this defect was
cosmetic — a missing signal. Under the swap it is a **wrong state transition**:
the pane goes blank for up to two minutes at exactly the moment the operator is
waiting for the deliverable. Its fix is already specified in its own doc and is
the first code to touch.

## The work, blocker and free wins first

1. **Fix turn-end-still-marks-done.** Prerequisite, ~10 lines, fully specified.
   Correct that doc's line citations while in there; every one of them is stale.
2. **Observe `stop_reason`.** Instrument `scripts/verify_questions.py` — matching
   the app's `include_partial_messages=True` rather than the script's current
   `False` — and take one real turn that calls a tool and then answers. Capture
   `stop_reason` per message, `ResultMessage.result` on the success path, whether
   the turn ends in a question or a statement, and the wall-clock gaps between
   assistant messages. Those four settle, in order: whether staging is a field
   read, whether the deliverable text arrives free, whether obligation-only
   filtering does real work, and how much the working state has to carry.

   **Instrumented 2026-08-14; the live run is still owed.** The script now sets
   `include_partial_messages=True`, records every message in arrival order with
   its `stop_reason` *and that value's type*, and carries a third `tool` case —
   read a file, then answer — because a turn with no tool call produces one
   assistant message and discriminates nothing. It distinguishes three outcomes
   the doc above collapses into one: the CLI sent `null`, this SDK build has no
   such field, and this message kind has no stop reason to give. It also reads
   `stop_reason` off the streaming `message_delta` events, which is a second
   independent answer if the complete message comes back `None`.

   ```
   .venv/bin/python scripts/verify_questions.py --selftest      # no API, no tokens
   .venv/bin/python scripts/verify_questions.py --case tool     # the measurement
   ```

   `--selftest` drives the same recording and reporting path with synthetic
   messages, so the branches the live run depends on have already executed. Paste
   the `--case tool` output back; the verdict under measurement 1 is the one that
   decides whether step 4 can be built at all.
3. **Render the sub-agent deliverable.** `Translator` receives
   `last_assistant_message` from `SubagentStop` — a sub-agent's whole final
   answer as one string — and reduces it to `splitlines()[0][:80]` for a progress
   topic, discarding the rest. Sub-agents do not stream and emit no
   `ResultMessage` for their own node, so that field is the only deliverable one
   will ever produce. Already confirmed present and non-empty by the live run in
   2026-08-13-a-card-is-an-agent. Independent of everything above and the
   cheapest end-to-end proof of the model.

   **Built 2026-08-14.** `SubagentDelivered` carries the whole string beside the
   unchanged one-line `SubagentProgress`; `AgentRecord.deliverable` holds it; and
   `detail._nothing_selected` renders it whole through the block renderer, in
   place of the narration, for a settled node that has one.

   The deliverable is **not** also appended to the sub-agent's `Transcript`, and
   it replaces the narration rather than sitting above it, for the same reason: a
   sub-agent's own `AssistantMessage`s do arrive on the parent stream carrying
   `parent_tool_use_id`, and `_assistant` routes their text into that node's
   transcript, which is what `turn_prose` and so the narration read. Appending
   would print the answer twice in CONTEXT and drawing both bodies would print it
   twice in DETAIL — the "a one-character answer came back as `99`" hazard that
   `already_streamed` exists to prevent, in two new places.

   **What is actually measured, and what is not.** The live run in
   2026-08-13-a-card-is-an-agent counted `messages_with_usage: 2` under a
   sub-agent's `parent_tool_use_id`. That establishes only that **two messages**
   for that sub-agent reached the parent stream — every `AssistantMessage` carries
   usage, including one whose content is a single `ToolUseBlock`. It does not
   establish that any of them carried a `TextBlock`, so it is consistent with the
   sub-agent's transcript holding tool calls and no answer at all, which is
   precisely the case where refusing the append is wrong. The refusal rests on the
   routing being *reachable*, not on it having been observed carrying the answer.

   **Open, and worse than "the answer is missing".** If the adjacency join fails —
   `subagent_by_tool_use` without the spawn's `tool_use_id`, which that doc lists
   as an unmeasured hypothesis under concurrent spawns — then `_node_of` falls back
   to the root for *every* message, so the sub-agent's transcript is not merely
   missing its answer, it is empty. CONTEXT shows nothing and `record.deliverable`
   is the only copy in the store. `scripts/verify_subagent_usage.py` now captures
   what would settle both questions in one run — text blocks per sub-agent message,
   and `last_assistant_message` against the sub-agent's own accumulated text — as
   its Q4 section.

   **That measurement has not been run, and until it is, the comment in
   `_subagent_stop` declining the append is a reading and not a measurement.** It
   rests on the routing being *reachable* — the code path exists and
   `test_subagent_output_lands_in_the_subagents_own_transcript` pins it — not on the
   answer having been observed arriving through it. Do not inherit this as settled.
   If Q4 reports that no sub-agent message carried a `TextBlock`, the refusal is
   wrong and the append is required, because then nothing else on that node holds
   the answer at all.
4. **Boundary markers**, staged by the switch above.
5. **The two-state pane**, including the collapsed-header history.
6. **Delete `_narration`, `narration_tail`, `_NARRATION_LINES`,
   `narration_follow`.** Nine tests, not five: beyond the `narration_tail` tests,
   `_narration` is monkeypatched *by name* in `tests/test_detail.py`'s draw
   harness, which raises `AttributeError` once the attribute is gone. One of those
   pins the board-before-narration ordering and says in its own docstring that it
   was "caught by a screenshot, not by a test" — whatever fills the working state
   inherits that child-window constraint with nothing pinning it.

## Not doing, and why

- **A new `SegmentKind` for comments.** Three reasons above, any one sufficient.
- **Touching `DetailState.prose_blocks` or `_question`.** Already the buffered
  whole-render this doc is asking for. The obligation path needs no change.
- **Touching CONTEXT, the deltas, or the markdown streaming stack.**
  `live_block`, `with_live_inline` and the never-revised invariant serve
  `RenderMode.RICH` alone, which stays. The 08-11 doc recorded that removing
  narration would leave nothing streaming markdown; that is still true and still
  not a reason to collapse `BlockCursor`.

## Open

- **Cancelled and failed turns.** Partial bytes sit in the transcript with no
  `stop_reason` to stage them. Holding the comment list in past tense is probably
  right — it is the last true thing about that turn — but that makes comments not
  purely ephemeral, which is a rule rather than a default.
- **What the working state shows before the first comment.** A turn that thinks
  and calls tools with no preamble produces no comments at all, and that is the
  case narration existed for. Whatever fills it must render as *status* — elapsed
  time, phase, sub-agent roster, cost — and never as prose, or it competes with
  the deliverable's register and the step function is gone again. Contents
  undecided; step 2's timing data should inform it.
- **"Card" is being redefined.** DETAIL-per-card is the decision, but the shape of
  a card is in flight, so this needs re-evaluating at implementation time rather
  than being designed against the current one.
- **Habituation.** If nearly every turn ends in a question, obligation-only
  filtering is not filtering anything and the signal degrades over a long session
  regardless. Step 2 measures the ratio.
