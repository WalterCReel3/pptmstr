# The layout models the store, not the operator

**Dated:** 2026-08-10 · **Status:** **C built, as A first** — both arrangements run;
see "What was built" at the end · **Follows:** dogfooding

## The diagnosis

There are seven dockable windows (`app.py:354-362`) and they are, one for one, the
seven nouns in the codebase: agents, review, detail, transcript, log, launch, talk.
Each panel renders exactly one data structure. That is why the UI reads as raw and
disjointed — **the arrangement encodes the store's shape rather than the operator's
loop.** It is a schema browser for `Snapshot`.

The store is not the problem. `Snapshot.review_queue` (`store.py:323`) is already a
cross-agent projection sorted by wait time — the model *knows* the operator works a
backlog. The layout does not.

Six specific consequences, in descending order of cost.

### 1. Two mechanisms for one obligation, and only one has a surface

`AWAITING_APPROVAL` enters `review_queue` and gets a pane. `AWAITING_INPUT` — the
state built specifically because a question looked like a finish
(planning/2026-08-10-conversational-sessions.md) — appears only as a `YOUR TURN`
badge in the tree, and is answered in a different pane. `FAILED` gets nothing.

These are the same operator obligation: *something is waiting on you*. They are
modelled by two different UI paths and counted by neither. The status bar says
"3 awaiting review" while a fourth session sits on an unanswered question.

This is the cardinality/authority mismatch from the dogfooding post-mortem,
reproduced at the presentation layer: two sides modelling the same fact, nothing
reconciling them.

### 2. Two cursors that can disagree, silently

`state.view.selected` is a `NodeId`; `state.review.selected` is a pending-approval
id. `DETAIL` follows one, `TRANSCRIPT`/`TALK` follow the other. Nothing constrains
them to agree, and no pixel indicates when they don't — so `a` can approve a write
from one agent while the transcript you are reading belongs to another. `j`/`k`
also mean different things depending on which pane owns focus.

### 3. The same fact rendered three times, unlinked

A pending call appears as a `REVIEW` badge in the tree, a row in the queue, and a
diff in `DETAIL`. Three resolutions of one thing, in three dock nodes, with no
visual thread between them. The eye has to do the join.

### 4. Space is allocated inversely to importance

`AGENTS` gets ~55% of the window for a six-row fixed-column table that is mostly
empty at any realistic N. The approval queue — the entire premise — gets a
scrolling three-row list in a bottom tab, currently behind `LOG`.

### 5. `DETAIL` is misnamed, so session health has no home

It is not the detail of the selection; it is *the selected pending approval*. That
leaves nowhere for the facts a node actually carries. `UsageRollup` accrues on every
message and is rendered by no widget. `cwd` is a launch field that disappears after
launch. `transcript_path` is recorded (`driver.py:397`) and never read. These aren't
missing features so much as facts with no pane to live in.

### 6. There is no project axis

The README claims one window drives work across several projects. `cwd` is chosen in
`LAUNCH` and then invisible. Twelve sessions across three repos render as a flat
list of twelve. Nothing in the layout expresses the thing the tool is being sold on.

---

## What the operator's loop actually is

Everything below is arranged around this rather than around `Snapshot`:

1. **Dispatch** — start work, somewhere, with a cwd and a model.
2. **Answer** — clear the things blocked on you. Dominant activity by time.
3. **Steer** — go into one conversation and push it.
4. **Watch** — is anything stuck, expensive, or about to compact.
5. **Reap** — close, retire, fork.

Current mapping: (4) gets the most space, (2) is split across three panes and a
background tab, (3) is split across two panes, (1) is a background tab, (5) is three
buttons at the bottom of `TALK`.

---

## Four layouts

They are genuinely different bets, not variants. Each states what it optimises and
what it gives up.

### A — Inbox: the queue is the application

**Bet:** the operator is a bottleneck by design, so the screen should be the
bottleneck's work surface and nothing else.

```
┌ menu ─────────────────────────────────────────────────────────────────┐
│ ┌ SESSIONS ─┐ ┌ NEEDS YOU  (4) ──────────────┐ ┌ CONTEXT ───────────┐ │
│ │ pptmstr   │ │ ▸ Explore   2m  Write demo.txt│ │ transcript of the  │ │
│ │  ● Explore│ │   ┌──────────────────────────┐│ │ item under the     │ │
│ │  ◐ review │ │   │ --- /dev/null            ││ │ cursor, tailed     │ │
│ │ orbital   │ │   │ +++ /tmp/demo.txt        ││ │                    │ │
│ │  ● session│ │   │ +hello                   ││ │                    │ │
│ │  ○ idle   │ │   └──────────────────────────┘│ │                    │ │
│ │           │ │   [approve] [reject] [edit]   │ │                    │ │
│ │           │ │ ▸ session   4m  ❓ asked you   │ │                    │ │
│ │           │ │ ▸ code-rev  1m  Edit theme.py │ │                    │ │
│ │           │ │ ▸ session   —   ✗ failed      │ │                    │ │
│ └───────────┘ └──────────────────────────────┘ └────────────────────┘ │
│ ⌨ task……………………………………………… cwd ▾  model ▾  [launch]                     │
└ 6 agents · 4 need you · oldest 4m · 2/4 sessions ──────────────────────┘
```

- One centre pane merging **approvals, questions, and failures**, oldest first.
- The cursor row **expands in place** into its own affordance: a diff with
  approve/reject/edit for a tool call, a composer for a question, the error and a
  retry for a failure. `DETAIL` disappears as a concept — the row *is* the detail.
  *(Amended below: the row is where every **decision** is made, but it is not where
  the detail fits. `DETAIL` is back as a tab-mate of `CONTEXT`, deriving from the
  same cursor and unable to move it.)*
- Right pane is not independently selectable: it follows the queue cursor. **One
  cursor exists.** Defect 2 becomes structurally impossible.
- Left rail is a status strip, not a table — glyph, name, context ring, grouped by
  project. Scanning, not browsing.
- Launch is a single omnibox line, always present, never a tab.
  *(Superseded by `2026-08-10-launcher-as-a-modal.md`: it is `Ctrl+N` and a modal.
  The reasoning here holds against a tab; it did not account for the pane being
  absent from FOCUS entirely.)*
- **Zero state is the feature:** an empty queue turns the centre pane into "nothing
  needs you — here is what everyone is doing, and here is the launcher."

**Gives up:** steering one conversation is second-class. You get a transcript
sidebar, not a workspace. Needs `Enter` on a session to escape into B.

#### A's left rail: session cards, resolved 2026-08-10

The rail is dense cards, not a status strip. The argument is that the rail and the
inbox want **two different orderings over the same set**, and only cards can hold
the second one:

- **Inbox = urgency order.** Oldest obligation first, across sessions. Reorders
  constantly.
- **Rail = stable spatial order.** Project, then spawn order. **Never re-sorts.**

A card grid earns its space only if position is stable enough to build muscle
memory. Sorting cards by urgency would produce motion instead of a map and leave
two inboxes, one of them worse. Urgency rides on the card as a badge, never as
position.

**The rail is a second input device onto one cursor, not a second selection.** The
inbox cursor is authoritative; the card highlight is derived from it. Clicking a
card moves the inbox cursor to that session's oldest obligation. It does not
filter — a filtered inbox can hide the oldest item in the app while still looking
like the inbox, which is defect 2 coming back in a new coat. Scoping is available
one level up, on the project header, where it is a deliberate act and can announce
itself.

Clicking a card with no obligations has nothing to move the cursor to; that is the
gesture that should open FOCUS (option C).

This mostly absorbs option D — a card rail with project headers is the swimlane
view folded into one column.

### B — Workspace: the conversation is the application

**Bet:** the unit of work is a session, and an approval is part of that
conversation rather than a side channel.

```
┌ menu ─────────────────────────────────────────────────────────────────┐
│ ┌ SESSIONS ─┐ ┌ session · pptmstr ───────────┐ ┌ HEALTH ────────────┐ │
│ │▾ pptmstr  │ │  …reasoning, streaming…      │ │ sonnet-5           │ │
│ │  ●Explore │ │  Read(store.py)  268 lines   │ │ ~/Source/pptmstr   │ │
│ │  ◐code-rev│ │ ╔═ wants to Write demo.txt ═╗ │ │ ◕ 90k · 44k left   │ │
│ │▾ orbital  │ │ ║ +++ /tmp/demo.txt        ║ │ │ $0.42 · 61k tok    │ │
│ │  ●session │ │ ║ +hello                   ║ │ │                    │ │
│ │  ○idle    │ │ ║ [approve] [reject] [edit]║ │ │ sub-agents         │ │
│ │           │ │ ╚══════════════════════════╝ │ │  ◐ Explore   1m    │ │
│ │           │ │  …continues after you answer │ │  ✓ code-rev  done  │ │
│ │           │ │ ┌ reply ────────────────────┐│ │ [interrupt][close] │ │
│ │           │ │ │                           ││ │ [fork]             │ │
│ └───────────┘ └──────────────────────────────┘ └────────────────────┘ │
│ 4 need you › next: code-reviewer, 1m  [Tab]                            │
└───────────────────────────────────────────────────────────────────────┘
```

- **The approval renders inline in the transcript, at the point it occurred.**
  This is the idea worth taking from this option: causality becomes the layout.
  You see what the agent was reasoning about immediately above the call it wants
  to make, instead of correlating two panes.
- `DETAIL` + `TRANSCRIPT` + `TALK` collapse into one pane. Scrollback and composer
  stop being separated by a dock boundary.
- `HEALTH` finally gives cost, cwd, model, context headroom and the sub-agent tree
  somewhere to live, and puts interrupt/close/fork next to the facts you'd base
  them on.
- Throughput is preserved by a **persistent obligation strip** with `Tab` = jump to
  the next thing that needs you, across sessions. That single binding is what keeps
  B from being slower than A on a full queue.

**Gives up:** N-at-a-glance. You are always looking at exactly one session.

### C — Two modes: TRIAGE and FOCUS

**Bet:** A and B are both right, for different halves of the loop, and forcing one
arrangement to serve both is what produced the current compromise.

`hello_imgui` supports this natively — `RunnerParams.alternative_docking_layouts`
plus `hello_imgui.switch_layout(name)`, both present in the installed API. Two
`DockingParams`, one keybind. The panels are the same objects in both.

- `TRIAGE` = A. `FOCUS` = B. `Tab` triages, `Enter` focuses, `Esc` returns.
- The **left rail is in both layouts, in the same place**, and selection survives
  the switch. That shared anchor is the whole mitigation for mode disorientation;
  without it this is worse than either A or B alone.
- Auto-suggest, never auto-switch: when the queue empties, hint at FOCUS in the
  status bar. Yanking the layout out from under someone mid-diff is the failure
  mode to avoid.

**Gives up:** two layouts to keep coherent, and a mode is a real cognitive cost.

### D — Board: swimlanes by project, columns by obligation

**Bet:** at a dozen sessions over three repos, spatial standing beats any list.

```
│         NEEDS YOU (4)      WORKING (3)      IDLE (2)       ENDED (3)  │
│ pptmstr [Explore  2m ]    [session   ]     [session  ]    [Explore ✓] │
│         [code-rev 1m ]                                    [review  ✗] │
│ orbital [session  4m❓]    [Explore   ]     [general  ]    [session ✓] │
│ scratch                   [session   ]                                │
```

- Columns are **obligations, not the `AgentState` enum** — nine states is a data
  model, four columns is a workload.
- Cards move between columns as state changes; the project axis is the row.
- Clicking a card opens B as an overlay.
- Card ordering must key off `NodeId`, never position (I6), or hover and focus
  scramble every time a card moves.

**Gives up:** dead space at small N, and it is the most work. Best treated as a
later addition once the project axis exists, not as the first move.

---

## Recommendation

**C, built as A first.** A and B are each half-right; the library makes carrying
both nearly free, and building either one alone means rebuilding it when the other
arrives.

Build A first, because A's core move is the one that fixes a real model gap rather
than rearranging pixels. B's inline-approval idea is the second increment and is
what makes FOCUS worth switching to.

D stays on the shelf until the project axis exists and N is routinely above ten.

## What has to change underneath, regardless of which is chosen

These are not layout work and they gate all four options.

1. **`Snapshot.review_queue` → `Snapshot.needs_you`.** One projection over pending
   approvals, `AWAITING_INPUT` nodes, and `FAILED` nodes, sorted by wait. A tagged
   union of obligation kinds, not three lists. This is the fix for defect 1 and it
   makes the status-bar count truthful for the first time.
2. **One cursor.** Replace `view.selected: NodeId` + `review.selected: str` with a
   single focus value that is *either* a node or an obligation, with the other
   derived. Defect 2 is not fixable by discipline.
3. **A project key.** Derive from `cwd` — enclosing git root, falling back to
   basename. Presentation-level derivation is enough; no new store entity is needed
   yet, and inventing a `Project` record before the grouping proves useful would be
   backwards.
4. **Surface `UsageRollup`.** The data has been accruing since step 3 with no
   reader. Per §2.4 it belongs next to context as a *separate* axis, not merged
   into the same widget.

## What the mock settled

`scripts/mock_cards.py` renders the A layout from a fixture, with the real palette
and real font metrics. Throwaway — delete it when the layout lands. Four things
came out of looking at pixels that were not visible in the sketch.

1. **The card's first line must be the task, not the node name.** Every root is
   called `session`, so the name is the one string on a card that cannot tell two
   of them apart. `AgentRecord.task` already holds this and nothing renders it.

2. **Focus must be an outline, never a fill.** Filling the current card with
   `P.selection` was the obvious choice and it is wrong: every other mark on a card
   is a saturated state colour, and in `high_contrast` the selection fill is
   saturated too, so the sub-agent pips and the state label lose their
   figure/ground the moment a card becomes current. Constant fill, `P.focus`
   outline plus an edge bar. Only rendering all three required themes showed this.

3. **Fixed card heights per density class, two classes.** Active-or-blocked gets
   four lines; ended gets one. Seven full-height cards already fill a 950px rail,
   and variable heights would rule out `ListClipper` — which the rail is the one
   pane that will eventually need.

4. **A mock can render a state the real thing must never produce.** The zero-state
   view initially showed "nothing needs you" beside rail cards still carrying
   REVIEW badges, because the two halves were faked independently. That is the
   defect-1 failure in miniature, and it is the argument for both surfaces
   deriving from one `needs_you` list rather than being assembled separately.

### At twenty sessions across five projects

`--sessions 20`. Everything above held; five more things only appear at scale, and
the first two are the ones that would have shipped.

5. **Anything that counts obligations must count the union, not the approvals.**
   The project header read `ORBITAL · 6 sessions` with no waiting count while that
   project held a question *and* a crashed session — because the counter summed
   approvals. This is defect 1 reproducing itself **inside the fix for defect 1**,
   written by someone who had just spent an afternoon on why that is wrong. The
   habit of treating an approval as the only kind of obligation is in the fingers.
   Whatever `needs_you` ends up being, every count and badge reads it and nothing
   re-derives from `pending`.

6. **Identity by task is needed in the inbox too, not just on the cards.** The
   inbox's first column read `session` on six rows of eight. The fix that landed
   for cards did not generalise because the inbox was only ever looked at with
   seven sessions in one project. Session title identifies; `project / sub-agent`
   qualifies.

7. **Two density classes is wrong; it needs three.** Splitting on terminality put
   working-but-not-blocking sessions — the majority at N=20, and the ones least
   likely to be acted on — at the same four lines as a blocked one. Height should
   track obligation: blocked 4, active 2, ended 1. This is what lets ~13 sessions
   share the rail with no loss of the ones that matter.

8. **The rail must scroll itself to the focused card.** Two inbox rows referred to
   `vendor-sync`, a project whose cards were entirely below the fold. A derived
   highlight that the operator cannot see is not a highlight, and this is the one
   failure mode that makes the single-cursor design *look* broken while being
   correct. `set_scroll_here_y` on the focused card, every frame it moves.

9. **Three panes at 1600px starve the inbox.** Identity, wait and call summary all
   compete for what is left after the rail and the context pane, and the summary is
   what loses. Narrowing the context pane to 0.32 bought it back, but the real
   reading is that TRIAGE does not need a transcript pane at that width — the
   expanded row already carries the diff. Another argument for C: the two modes
   want different splits, not one compromise.

Two unrelated defects surfaced while building it:

- **`_waited` and `elapsed_cell` overstated every duration.** Both formatted
  minutes as `f"{seconds / 60:.0f}"`, which rounds; the seconds field beside it
  truncates. Any remainder ≥30s rendered a minutes field one too high — a 3m40s
  wait read as "4m40s" in the approval queue. Fixed, with the arithmetic pulled out
  as `widgets.format_elapsed` so it is testable without an ImGui context
  (`tests/test_durations.py`).
- `ImDrawList.add_rect` takes `(col, rounding, thickness)` in this binding, not the
  C++ `(col, rounding, flags, thickness)` — the same transposition `widgets.py`
  already documents for `path_stroke`. Worth adding to §7's trap list.

## What was built

C, as A first, in one pass. `TRIAGE` and `FOCUS` are two `DockingParams` over one
set of panel objects, switched with `Tab`/`Enter`/`Esc`; the rail is in the same
place in both and the cursor survives the switch. `--layout` starts in either.

The four prerequisites landed first, and all four were load-bearing rather than
tidy-up. Five things came out of building that the plan did not have.

1. **Two of the three obligation kinds had no timestamp.** `needs_you` sorts by
   wait; an approval carries `requested_at` and a failure carries `ended_at`, but a
   turn that ended with a question carried nothing — so the obligation most likely
   to be forgotten was also the one that could not be aged. `AgentRecord.state_since`
   now records when a node entered its current state, stamped from the frame clock:
   `Store.apply_all` takes `now`, `_apply` keeps it as a required argument, and the
   comparison happens in one place after the match rather than in the six arms that
   can change a state. A timestamp on four intents would have been more honest to
   the emitting thread by a few milliseconds and would have had twelve call sites to
   forget.

2. **A failure is the one obligation with no natural resolution.** An approval is
   answered and a question is replied to or closed; a crashed session has already
   ended, so it would have sat in the queue forever. `FailureAcknowledged` is a
   store intent rather than a UI filter, because `needs_you` is built in the store
   and every count reads that one list — filtering in the pane would have left the
   status bar reporting obligations the inbox no longer showed, which is the exact
   disagreement the projection exists to remove.

3. **A default cursor position and a chosen one must not behave alike.** Found by
   screenshot, not by reasoning: on first run the cursor settled on a node during
   the seconds before any agent asked for anything, and then never yielded when work
   arrived. The rail highlighted a card, the inbox listed four rows and expanded none
   of them, and every pixel looked correct. `OnNode` now carries `pinned` — an
   explicit click holds (or `FOCUS` would be unusable whenever anything is queued),
   an unchosen landing spot yields to the queue.

4. **The cursor is a key, never an index.** `needs_you` is age-sorted and reorders
   whenever anything arrives, so a positional cursor retargets itself between frames
   — and this is the cursor that decides which agent `a` applies to. Every obligation
   carries a stable `key`; the remembered index is consulted only to decide where to
   land *after* the key stops matching, which is what lets a run of approvals be
   worked without the cursor jumping back to the top.

5. **`cwd` needed store plumbing, not just derivation.** The proposal said
   presentation-level derivation was enough and no new store entity was needed. True
   about the entity, incomplete about the wiring: `cwd` lived on `AgentSession` and
   appeared on neither `AgentSpawned` nor `AgentRecord`, so nothing on the UI thread
   could see it. The mock did not surface this because it fabricated its own
   `project` field. Sub-agents inherit it in the store, so no emitter has to remember
   to pass it.

Also settled while building: one duration formatter, not two — the queue used to
render past an hour as bare `1h` while the tree rendered `1h00m`, and the inbox's
wait column has room for the longer one. `ui/tree.py` is deleted; the rail replaces
it in both arrangements and a pane with no home is the `fake_driver.py` mistake
again.

**Not yet done, and known:** `max_budget_usd` is still unwired, so `HEALTH` reports
spend without being able to cap it. `scripts/mock_cards.py` is now throwaway that
has outlived its purpose and should be deleted per its own docstring. No real
multi-project run has driven either arrangement — everything above was verified
against the fake driver and the test suite.

## Amendment: DETAIL comes back as a tab, from dogfooding

**Dated 2026-08-10, after the first real run.** The expanded row does not carry the
detail. §9 above concluded that "TRIAGE does not need a transcript pane at that
width — the expanded row already carries the diff", and used that to justify
narrowing `ContextSpace` to 0.32. The first half of that is wrong, and the reason it
looked right in the mock is instructive.

The row clips in **two** places, and only one of them is about pixels:

1. **Pixels.** `ellipsis` on title, qualifier and summary; fixed-height bordered
   bodies; and the no-diff fallback at `inbox._expand_approval` renders
   `f"  {key} = {value!r}"` with no wrap, so a Bash command runs off the right edge
   of a pane that was deliberately made narrower.
2. **The source.** `approval.summarize` clips to 90 characters *before the store
   sees the string*. For a `Bash` call the row's summary is a lossy rendering of
   `raw_args["command"]` and **no window width recovers it** — it has to be re-read
   from `raw_args`. Every fixture in `fake_driver._TOOLS` was short enough that this
   never showed. That is the mock lesson from §"What the mock settled" repeating: a
   fixture that cannot produce the bad state certifies the wrong thing.

`ui/detail.py`, docked into `ContextSpace` in TRIAGE (in front of `CONTEXT`) and
`HealthSpace` in FOCUS (behind `HEALTH`). Wrap is always on; that is the pane's
entire identity, not a toggle.

**This is not the DETAIL the inbox replaced.** Defect 2 was never about a pane
existing — it was about `review.selected` being *independently assignable*. This
pane reads `focus.obligation` and offers no way to move it, exactly as `CONTEXT`
does. Nothing here can disagree with the inbox because there is nothing here to
disagree with. The name is reused deliberately rather than invented around, because
"the detail of the thing under the cursor" is what it is.

Four things settled while building it.

1. **A tab, not a split.** The 0.32 width was chosen so identity, wait and summary
   still fit in the inbox. A fourth split would take that back to fix a problem that
   costs nothing as a tab-mate, and `LOG` already proves the space carries three.

2. **It renders all three obligation kinds, not just approvals.** A pane that went
   blank on a question and a failure would be defect 1 in miniature — and per §5
   that habit is in the fingers. `_KIND_LABEL` is keyed on `ObligationKind` and a
   test asserts the mapping is total, so adding a fourth kind breaks the test rather
   than silently blanking the pane.

3. **Every bound announces itself.** Wrapping rules out `ListClipper` (same
   collision `transcript_pane` documents), so the diff is capped at 1200 lines and
   an argument value at 20k characters. Both print what they dropped. A pane whose
   premise is "nothing is lost" must not quietly lose things at the bottom, and a
   silent cap reads as "that was all of it".

4. **A string argument is rendered raw, not `repr`.** `repr` turns a 200-line
   `Write` content into one wrapped paragraph of `\n` — the exact loss the pane
   exists to undo, reintroduced by the formatting. Non-strings keep `repr`, where
   the quoting is the information.

Also: a `copy` button, because ImGui text is not selectable and the first thing an
operator wants to do with a long Bash command is paste it into a shell before
approving it. The clipboard rendering is unbounded — the caps above are about frame
cost, and the clipboard has no frame.

`fake_driver._TOOLS` gains one deliberately long `Bash` fixture. It is the only long
entry, and its absence is why this took a real run to notice.

**Verified:** lint, black, mypy, 325 tests, and four screenshots — the pane in both
TRIAGE and FOCUS against the fake driver, all three obligation kinds over a
hand-built snapshot at 478px (a wrapping `Bash` command, a two-paragraph question, a
wrapped traceback), and an A/B of `imgui.indent` against a wrap. Not verified: any
window width other than 1500 and 478, and no real multi-project run.

The indent A/B is worth recording because it settled a *reported* defect that was
not one. The first screenshot read as though wrapped continuation lines lost their
indent, which would have made the arguments block misalign under exactly the long
values the pane exists for. Rendering the same string with and without `indent(40)`
shows continuations align correctly on both — the apparent 8px was a glyph artefact
of a downscaled grab. Cheap to check, and the alternative was rewriting the
arguments block around a defect that did not exist.

**Left alone on purpose:** the row's own clipping. Two surfaces with opposite rules
about width beats one surface compromising between them — the inbox is scanned and
clipping is what makes scanning work.

## Not proposed, and why

- **A command palette.** Tempting and wrong here. The actions are few and already
  have single keys; a palette would add a layer of indirection over `a`/`r`/`e`
  without removing anything.
- **Persisting per-project layouts.** More state to get out of sync for a benefit
  nobody has asked for. Revisit if D ever ships.
- **Making `LOG` prominent.** It is a debugging surface. It should be reachable,
  not resident — its current tab-mate status next to `REVIEW` is precisely the
  inversion being corrected.
