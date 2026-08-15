# "What it said" is a byte tail, so the reading surface carries the fluff

**Dated:** 2026-08-11 · **Status:** steps 1–4 done · **Follows:**
2026-08-10-layout-proposals, 2026-08-10-transcript-markdown

## The rule this settles

The layout doc justifies DETAIL by clipping discipline: "two surfaces with
opposite rules about width." That is true and it is the weaker reason. The rule
that came out of dogfooding TRIAGE is:

> **The inbox row is where you act. DETAIL is what informs the act.**

It predicts more than the width rule does, and three open questions fall out of it
immediately rather than needing to be argued on their own terms:

- **The composer stays at the row.** Not because a second one would reintroduce
  defect 2 — it would not, since a DETAIL composer would send to
  `obligation.node` and has no way to point anywhere else — but because typing is
  acting. The row already holds `send`/`interrupt`/`close` and the draft state
  keyed by node; splitting the reply away from them buys nothing and costs a
  second `ComposeState` that can silently drop a draft.
- **CONTEXT does not split in two.** An earlier proposal in this conversation was
  to split the transcript pane into a "conversation" pane (`OUTPUT` + `SYSTEM`)
  and a "work" pane (everything else). Rejected: under the rule, the prose that
  informs the next move belongs *at the decision*, in DETAIL, not in a second
  stream pane that would have to be correlated with the first by eye. Two panes
  over one stream also have two scroll positions and nothing relating them —
  `Line` carries `run`, which is cache-local, not a global position.
- **What remains of the prose/machinery distinction is a filter on CONTEXT**, not
  a pane boundary. Cheap, reversible, and it cannot drift out of sync with
  anything. `_visible()` already filters by kind at line granularity.

CONTEXT is therefore archaeology: the surface you go to when DETAIL did not
answer it. That is a demotion, and it is the correct one.

## The defect

`detail._question` renders `record.transcript.tail(12_000)` under the heading
"what it said". `inbox._expand_question` does the same with 1200. `Transcript.tail`
is a **byte** window and is kind-blind, so what lands in both surfaces is the last
N bytes of *everything* — reasoning, tool calls, tool results, compaction notices.

A question preceded by a large tool result renders the tool result as the
question. The pane whose entire job is to inform the next move is showing the
machinery instead of the prose, and the heading asserts otherwise.

This is the same defect in two places from one source, which is why the fix is in
`Transcript` rather than in either pane.

## The precondition, verified rather than recalled

Scoping prose to *the current turn* needs a turn boundary, and `Transcript` has no
turn concept. It turns out not to need one.

`SegmentKind.SYSTEM` is appended by `AgentSession.send` (`driver.py:780`) and by
nothing else — it is the only `SYSTEM` append site in the codebase. Every root
turn goes through `send`, including the opening task (`run` calls
`await self.send(self.task)` at `driver.py:710`). Checked for ways to start a turn
without a marker:

- `interrupt()` calls `client.interrupt()` and never `query()`, so it starts no
  turn.
- `_user()` handles `UserMessage`, which is how the protocol carries tool *results*
  back — it writes only `TOOL_RESULT`/`ERROR`, so an echoed user message cannot
  forge a boundary.
- `send()` is the only caller of `client.query()`.

So **the last `SYSTEM` segment is where the current turn began.** No new primitive,
no plumbing, no timestamp. A sub-agent never goes through `send` and so carries no
marker at all — correctly, since its whole life is one turn, which makes "start of
buffer" the right fallback rather than an error.

I claimed earlier in this design conversation that this primitive was missing and
had to be built. That was recall, not a read. Recording the correction here because
the wrong version is cheaper to act on than to re-derive.

## The work, cheapest and most general first

Sequenced the same way the markdown work was, for the same reason: the last
commitment made should be the expensive one.

### 1. `Transcript.turn_prose()` — done

One reader on the transcript, used by both surfaces. Returns `OUTPUT` text emitted
since the last `SYSTEM` segment.

**Unbounded on purpose.** `tail`'s docstring warns against `text()` on a frame
path because its cost grows with history; a turn's prose is bounded by how much
the model said in one turn, which is not the same quantity. Callers bound it with
`detail.clip`, which announces what it dropped — a surface whose premise is "this
is what it said" must not quietly show less than that.

Pure, so it is testable without a GL context.

### 2. Both consumers read it — done

`detail._question` and `inbox._expand_question`. The row keeps tail semantics on
the clipped preview (the ask is at the end of a turn, not the start); DETAIL shows
the whole turn and announces any clip. `detail.plain_text` uses it unbounded — the
caps are about frame cost and the clipboard has no frame.

### 3. Markdown in DETAIL — done

`blocks.py` only markdown-parses `SegmentKind.OUTPUT` (`blocks.py:327`), so
turn-scoped prose is exactly what it wants, and DETAIL wraps unconditionally,
which is the precondition RICH needs and which CONTEXT has to toggle for.

**The cache question dissolved rather than being answered.** It was framed as
"share `TranscriptState`'s prune policy or duplicate it", and the answer is
neither: DETAIL needs no incremental state at all. A question is a *finished*
turn — the model has stopped and is waiting — so there is no live tail for
`live_block` to track, and DETAIL renders one obligation at a time, so its memo is
a single entry keyed by `(node, published_length)`. The key changing is the
eviction; there is no sweep to share or duplicate.

Sharing the cursor was also ruled out on its merits, which is worth recording
because it reads like the cheap option and is not:

- `BlockCursor` parses from line 0, so reaching the last turn costs the whole
  session — reintroducing exactly the scaling `turn_prose` was written to remove.
- It is fed only in RICH mode (`transcript_pane.py:99`), so the from-scratch parse
  is the *common* case for a question you are answering, not the rare one.
- `cache_for()` stamps `last_frame_touched`, so DETAIL touching it would hold the
  largest object this UI keeps alive for a node nobody is viewing.

**One primitive was genuinely missing, and it was not the one predicted.**
`feed()` finalises a block only when the *next* line proves it complete, so a
one-shot parse drops its trailing paragraph — which in a question turn is usually
the question. `BlockCursor.finish()` closes the open block, attaches inline like
any other finalised block (`live_block` deliberately does not), and reports an
unterminated fence as `closed=False`. General to any one-shot caller, not a DETAIL
special case.

Bounding is now `rich_pane.draw`'s `windowed()` alone; DETAIL's 12,000-character
cap is gone. Two caps over one body of text meant two policies and two differently
worded truncation notices, and the line-based one is the one that matches what is
actually drawn. Cost: the bound became tail-anchored, which for "what it said
before I reply" is the right end to keep anyway.

Step 1 alone removed the machinery from the reading surface, which was the
complaint. Step 3 makes what is left read well.

### 4. Narration in DETAIL's empty state — done, and superseded

Superseded by 2026-08-13-detail-swaps-to-a-deliverable. The measurement below
stands; the conclusion drawn from it does not. It asked what is affordable to
draw per frame, and the governing question turned out to be what an operator
notices — a continuously updating surface is one the eye habituates to, so
narration was spending the signal that a deliverable's arrival should carry.
Retained here because the costs were measured rather than argued and are still
the numbers to reason from.

A *running* session has no obligation, so `detail._nothing_selected` renders
"nothing waiting on you from X" and stops — dead space at exactly the moment its
narration would be worth seeing. Under the rule this is where mid-turn prose
belongs. It needs step 3 first, and it needs a decision about whether DETAIL
follows the node when the cursor is not on an obligation (it already does —
`focus.node`), so it is small but not free.

Step 3 unblocked it but did not hand it the answer, because step 3's premise was
that the turn is *over*. A running session is the case `DetailState.prose_blocks`
explicitly does not serve: the key moves every frame, so the memo degenerates to a
re-parse per frame.

**The retained cursor was measured rather than argued, and then not built.**

| | cost |
| --- | --- |
| full block parse | ~1.6 ms per KB of prose, linear — 4.4 KB = 40% of a 60 fps frame, 11 KB = a whole one |
| `turn_prose()` alone | 0.3 ms at 3k segments, 1.5 ms at 15k |

So a per-frame *parse* is not affordable and a per-frame *read* is. That is the
whole case for incremental parsing, and it only pays if the result has to be
markdown. It does not: dogfooding settled that CONTEXT's useful modes are raw and
wrap, and narration is the same activity — watching output arrive, which is what
wrapping is good at. Rich rendering is for prose that has stopped moving.

So narration is `turn_prose()`, tail-bounded by line, wrapped, pinned to the
newest line. No cursor, no turn-scoped line cache, no new `Transcript` method. The
two surfaces in this pane render differently because the reading differs, not
because one of them is unfinished.

`widgets.follow_tail` came out of it: the pin's disengage cannot be driven by
scroll position, because `set_scroll_here_y` re-pins every frame before any
position test could observe the operator leaving. CONTEXT already knew that;
having the knowledge in one place is what stops the two panes drifting.

**This leaves nothing in the codebase that streams markdown.** `live_block`,
`with_live_inline` and `BlockCursor`'s append-only-never-revised invariant now
serve exactly one caller, `RenderMode.RICH` in CONTEXT — the mode dogfooding says
is not earning its keep. If that goes, `BlockCursor` collapses to a one-shot
`parse_blocks(lines)`, `finish()` stops needing to exist, and the invariant that
forced setext headings and link reference definitions out of the parser
(`blocks.py:426`) stops being load-bearing. Recorded as available, not decided:
re-adding incremental parsing later is an invariant across the whole parser, not
a local change.

## Not doing, and why

- **Inline approvals in the transcript (B's idea).** Recorded in the layout doc as
  "what makes FOCUS worth switching to". FOCUS is not used in practice, so
  building it there is building for nobody. Building it in TRIAGE's stream instead
  would give three surfaces for one obligation — the row, the embedded block, and
  DETAIL — which is defect 3 reintroduced by the fix. It also collides with the
  clipper: a wrapped diff embedded in a clipped stream either takes the whole
  stream off `ListClipper` or needs its own scrolling child, which is DETAIL
  again, nested and narrower.
- **Rescuing FOCUS.** Its differentiator was never built, so "I do not use FOCUS"
  is not evidence that B was the wrong bet — it is evidence B never shipped. Left
  vestigial until something forces the question. Worth knowing that the join key
  for inline approvals already exists and is unused: `driver.py:246` stamps every
  `TOOL_CALL` segment with `("tool_use_id", block.id)` and
  `PendingApproval.tool_use_id` holds the same value, so locating a parked call in
  the stream is an exact lookup rather than a heuristic.

## Open

- Should the turn's **operator prompt** appear above "what it said"? It is a
  `SYSTEM` segment sitting immediately before the prose, it is genuinely
  informative for "what did I ask it to do", and it is one segment away. Left out
  of step 1 deliberately: it changes what the section means, and that is a
  decision to make on its own rather than to smuggle in with a filter.
