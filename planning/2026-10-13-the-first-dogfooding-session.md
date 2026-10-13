# The first dogfooding session: one crash, one drawing defect, and a launcher that says too much

**Dated:** 2026-10-13 · **Status:** the crash and the drawing defect are fixed on
`enhanced-sessions`, uncommitted, `make check` green at 6002 passed and 3 xfailed, read by no
reviewer; the launcher is a design note, nothing built · **Found by:** the operator,
running this branch against `~/Source/stm32-research`

Tags as in the sibling records: **(D)** the operator's words, **(R)** a recommendation, not
agreed, **(O)** open. "Measured" means the lead read it off the session's own files or the
operator's screenshots; nothing here was reproduced in a running pptmstr.

## What was said

**D1.** "There is a display artifact: when rendering the description of the session it's trying
to render the complete brief at a certain horizontal width."

**D2.** "The new task launcher is incredibly wordy and looks like it has no controls or options
for pre-creating a worktree (although to be fair that might need a 'wizard' interface). Maybe a
design note: density in the working screen. Some margin or 'air' for the dialogs."

**D3.** "And the session already crashed spectacularly."

## The crash

Session `03d986bc-9d27-4184-b366-ff5496098a0d`, a lead with a reviewer and a builder. All three
ended at 11:59:14Z with

    its session died: CLIJSONDecodeError: Failed to decode JSON: JSON message exceeded
    maximum buffer size of 1048576 bytes...

**Measured, from the transcripts under `~/.claude/projects/-home-wreel-Source-stm32-research/`:**

- At 11:59:14.269Z the reviewer called `Read` on a PDF that `WebFetch` had saved, `pages: "1-6"`.
  The tool result is one line of **1,287,886 bytes**: a text part and six page images as base64
  JPEG (188–280 kB each).
- The agent SDK reads the CLI's output a line at a time and refuses a line over 1 MiB
  (`_DEFAULT_MAX_BUFFER_SIZE` in `claude_agent_sdk/_internal/transport/subprocess_cli.py`).
  `ClaudeAgentOptions.max_buffer_size` raises it. pptmstr set it nowhere.
- **It had already happened.** The same session died the same way the afternoon before, at
  18:34:06Z, on two such reads (1,287,886 and 1,321,417 bytes). The operator resumed it; the lead
  put the board back and started a reviewer again; the reviewer fetched the same reference manual
  and read the same six pages. A resume could not have gone differently.
- So any agent that reads about six PDF pages in one call ends its whole session. `Read` allows
  twenty.

**What the operator then saw, and what was wrong with it:**

- Three failure rows in NEEDS YOU for one event, one per agent, each offering "retry in a new
  session". That is this branch's doing: since the last fix round a run that ends unasked
  settles its sub-agents as failed so the next lead can release their claims, and every failed
  node asks to be acknowledged.
- The sentence names an exception class and a byte count. It does not say that an agent read
  something too big, or that a retry will do it again.
- The board pane did its part: "2 stranded", each holder marked stopped, and the log's
  directory. A relaunch would let the new lead release both claims.

**Fixed, reproduced first** with the SDK's own transport over a scripted stream — the 1,287,886
character frame gave the screenshot exactly, three rows and all:

- `driver.MAX_STREAM_FRAME` is 64 MiB, passed as `max_buffer_size`. The number is twice the
  API's 32 MB request cap, because a tool result is only usable if it fits one request and a
  frame may carry it twice. It is a ceiling on one buffered line, not an allocation. In SDK
  0.2.164 a frame over the limit is always fatal; nothing skips one.
- A death by that limit is recognised by type and leads with a sentence in the operator's terms
  (one message was larger than the limit, most often an agent reading many PDF pages or images
  at once; a retry repeats it unless the agent reads fewer). The raw error follows.
- A sub-agent that was still working when its session failed ends cancelled, with the topic
  "ended with its session", and raises no row. Its claims are still releasable by the next lead.
  **(L)** "cancelled" is the state for something nobody cancelled; a state of its own would be
  more honest and needs the theme and the rail.

Not measured: what a real 20–60 MB frame costs in memory and time; whether the turn that
carries twenty page images then succeeds at the API — the session survives it either way.

The root's card read "902k". That is the CLI's tokens-until-compaction countdown, not tokens
used: about 65 k were resident, and the two sum to roughly the threshold of a 1M-window model
(by reading and arithmetic, not a live poll). The unlabelled number is a recorded choice in
`driver._context_from`. **(O)** whether a countdown with no label is what the card should show.

## The drawing defect

**Measured, from `Screenshot_20261013_075828.png` and `…080321.png`:** the task was typed on
three lines ("Where are we at with this?", a blank, "BTW this session is also dogfooding…").
The fleet card draws the first line as its title and the third line two rows lower, over the
card's state line and its usage line. NEEDS YOU hangs the same line under a row.

The cause is not the width: `ui/rail.py` cuts the title with `ellipsis`, which measures a string
and does not flatten its line breaks, so Dear ImGui draws the later lines wherever they fall.
Every one-line draw of free text had the same exposure. **Fixed** at `widgets.ellipsis`, which
now collapses every run of whitespace before it measures (`widgets.flat_line`), so each caller
is covered; the NEEDS YOU header hint, cut mid-word in the same screenshot, wraps at the pane's
width. Not seen drawn by anyone yet.

Nobody building this branch could have seen either: the session that built it had no display.

## The launcher: a design note

**(D2)** stands as the finding. What the launcher is today, from the images rendered on
2026-10-12: a 620-pixel modal whose lower half is prose — under `autonomous`, a red sentence, a
grey paragraph on the sandbox, a second on file writes, an amber one on the web, a third grey
one on sub-agents. Each sentence is there because a measurement or a review put it there, and
each was added without anyone looking at the whole.

**(R) Density is for the working screen; a dialog is where the operator decides, and deciding
needs air.**

1. **One line per fact, the argument behind a disclosure.** The modal states what will happen
   in a line each — *contained: writes only inside this directory* · *network: api.anthropic.com
   only* · *not contained: WebFetch, WebSearch* — and each line opens to the paragraph that is
   there now. Colour carries the one line that is a warning; nothing else is coloured.
2. **Margins.** Window padding and item spacing for modals set apart from the docks' — the
   docks stay dense. A rule between the task, the place it runs, and the gate.
3. **The controls first, the consequences after.** Task, directory, team and gate fit above the
   fold at the modal's default height; the boundary lines follow; the buttons never move.
4. **(O) Worktree creation is a second step, not a field.** `pptmstr/worktree.py` can detect,
   place, pre-flight and create a linked worktree and has no caller. It was held back on
   2026-10-12 for three reasons that still stand: there is no session name to default a branch
   from; it needs a detection worker and a hold; and whether a session can steer which config
   pptmstr's own unsandboxed git reads is unmeasured — a decoy outside the worktree that names
   it back is still reported as the main checkout, pinned by a test. A step that shows what was
   detected, where the worktree would go, on which branch from which base, and what the
   pre-flight found, and creates nothing until the operator presses create, is the shape that
   lets each of those be seen. It is a wizard in the plain sense: one decision per screen.
5. **(O)** Whether the same two rules — one line per fact, prose behind a disclosure — should be
   applied to the board pane's recording lines and the decision row, which were also written
   unseen.

None of this is built. It wants the operator's eye on a rendering before and after, which the
session writing this record does not have.
