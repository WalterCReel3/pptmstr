# R4: every option through every scenario, and the comparison

**Dated:** 2026-10-17 · **Task:** R4-scenarios (specification v2) · **Builds on:** [r1-code-cost.md](r1-code-cost.md),
[r2-prior-art.md](r2-prior-art.md), [r3-run-evidence.md](r3-run-evidence.md), and
[`../2026-10-17-two-leads-in-one-window-share-a-channel.md`](../2026-10-17-two-leads-in-one-window-share-a-channel.md)
(Decided 1–4 stand; Q5 is the question).

Sections 1–2 are evidence and mechanics. **Section 3, Judgement, is the only place an option is preferred.** Section 4 lists
what is still unknown.

## How to read this

**Tags.**
- **run**: printed by [`r4_explore.py`](r4_explore.py) (stdlib only; `python3 -I r4_explore.py`; run twice, byte-identical
  output; `black` and `mypy --strict` clean), or by re-running R3's script. Section numbers like *§x5* point at the
  script's output sections.
- **read**: read in the tree at `e57b027`, or in R1–R3's files.
- **fetched**: re-fetched by me this session.
- **relayed**: taken from R1–R3 and not re-checked by me.
- **model**: a count under a stated model, not a measurement of agents. Everything r4_explore.py prints is *run* as
  arithmetic and *model* as a claim about pptmstr.

**What I re-checked before building on it.**
- R1's load-bearing citations, by reading (read): `Snapshot.decisions_readable_by` (own, or all in the session for the
  root); `store._concern_wake` (no cross-session wake, deliberately); `app._send_wakes` (a wake for an absent session is
  let go of); `approval._AUTONOMOUS_AUTO = _REVIEW`; `Unplaced.NOT_THIS_SESSIONS`; boardlog's "**The format only
  grows.** … nothing is renamed, retyped or removed". Also `app.WAKE_WINDOW_S = 2.0`, which R1 did not cite: wakes
  coalesce only within 2 s. All held as R1 stated them.
- R3's figures, by re-running `r3_count.py` on the two folders (run): 41 messages over 233.29 min; needs-answer reply
  median 0.71 / p90 1.81 min; ack median 0.61 / p90 0.89 min; 37 of 42 messages carry another party. Identical to R3.
  The classification table inside it is still one reader's (R3's) reading, which I did not re-check.
- R2's quotes that the Judgement leans on (fetched):
  - S2, HTML: "Independent systems amplify trace-level errors 17.2× through unchecked error propagation"; "Centralized
    coordination, however, contains this to 4.4× by enforcing validation bottlenecks"; agent count "n_a: 1–4". The
    abstract: "architectures without centralized verification tend to propagate errors more than those with
    centralized coordination". R2 truncated the 4.4× sentence before "by enforcing validation bottlenecks". That clause
    matters below.
  - S18, AutoGen: "to limit the conversation to 25 messages to avoid infinite loop"; "A generative model (e.g., an LLM)
    selects the next speaker based on the shared context".
  - S1, MAST abstract: 1600+ traces, 7 frameworks, 14 modes in 3 categories. Confirmed. The DAG→cyclic figure
    (25.0% → 40.6%) I did not re-fetch, so it stays relayed.
    **Revised (R6; R7): now run.** R6 matched it in `pdftotext` of `mast.pdf` (2503.13657v3): the App. H.2
    sentence "modified the framework's topology from a directed acyclic graph (DAG) to a cyclic graph", and
    Table 5's ProgramDev-v0 row, 25.0 / 34.4 / 40.6. I re-grepped R6's extracted text and found both, plus §4's
    "+15.6% improvement" (run). The set is "ProgramDev-v0", not "ProgramDev". Its *meaning* was already
    withdrawn by R5 (finding 8: a supervised review loop). This revision changes only the figure's tag.
  - Everything else from R2 is relayed. These are still a small model's extractions, now checked twice.

**Options.**
- **O1** group channels;
- **O2** pairs plus coordinator leads, hierarchy by convention, forward by reference with a provenance chain;
- **O3** pairs with an enforced parent/child direction;
- **O4** pairs plus fan-out;
- **O5** shared state / blackboard (R1, R2);
- **O6** contract-net allocation (R2).

O2 has two shapes the operator could build, and they behave differently, so the script counts both:
- *O2-star/tree*, with coordinators;
- *O2-mesh*, pairs with no coordinator, which O2's symmetric links allow.

O5 and O6 are not costed in R1, and R2 calls O6 "an allocation protocol rather than a topology". I walk them through
every scenario anyway, more briefly.

### Axis A: authority kept apart from routing

R2's proposal is that *who may direct whom* is a different choice from *who may message whom*. Checked against the tree
and the Decided items (read):
- **pptmstr has already separated them.**
  - Decided 1: work landing on a board is gated by *that board's* rung.
  - Proposal item 3: work enters only through `declare_task`, which parks on reviewed rungs.
  - Decided 2: the charter is judged at the parked row.
  - So a message carries no authority in any option. Authority sits with the receiving board's rung and the operator.
- **What this does to the scores.** This is the point the spec asked about.
  - **O3 loses most of its "for" evidence.** S14 (Erlang supervision) and S6 (hierarchy resilient to faulty agents) are
    arguments for a hierarchy of *authority and escalation*. Decided 1–2 already provide that hierarchy, per board,
    whatever the routing. What O3 adds is enforcement on *routing*. The only "for" evidence about routing is the hub
    result (S2), which is O2's as much as O3's. S15 says Erlang deliberately does not enforce hierarchy on messaging.
    After the separation, O3 = O2 + a routing restriction whose benefit is loop-freedom (*§x6*) and whose costs are
    *§x7* and S-f.
  - **O1 loses its sharpest "against".** "Who is in charge of a group thread?" (S18's manager) is half authority and half
    turn-taking. Authority is answered already. Turn-taking is not: see S-c and S-j.
  - **The hub's error containment changes meaning.** S2 attributes it to "enforcing validation bottlenecks". In pptmstr
    the validation bottleneck is the operator's gate on a reviewed rung. That gate sits on the sender and on the
    receiving board in every option, not on a coordinator. So on reviewed rungs, S2's mechanism is present without a hub
    (inferred). On AUTONOMOUS nothing parks, a coordinator lead is the only possible validator, and S2 transfers better
    there.
    **Revised (S1 finding 3; R5):** neither the gate nor a forwarding coordinator is S2's mechanism (an orchestrator
    that cross-checks and aggregates outputs). See Judgement 2's revision.

### How far R2's numbers transfer

Every quantitative LLM result in R2 is about 1–10 agents inside one task with no human gate. S2's regression has 1–4
agents (fetched). A pptmstr lead is a long-lived agent with its own board, builders, rung and operator. I use those
results only for the *direction* of an effect, and only where the mechanism plausibly exists between leads:
- *Error containment by a validating hub:* transfers on AUTONOMOUS. It is redundant on reviewed rungs, where the operator
  is the validator.
- *Conformity to a majority (S7):* the mechanism needs a shared thread where a majority is visible. That exists in O1
  and not in pairs. Its size for leads is unknown.
- *Dense beats tree (S4, S5):* measured on solution quality in generation and research tasks. Lead-to-lead traffic in
  R3 was rulings and deliveries, not joint generation. Weak transfer.
  **Revised (S1 finding 7; R5, fetched + run).** MacNet's Table 1: Star 0.6267 vs Mesh 0.6316; Tree 0.6015, below
  Chain 0.6078; neither Star nor Tree differs significantly from Chain. MARBLE: "both the star and graph protocols
  yield similar task scores"; its multi-level tree was worst. So the evidence separates **one hub ≈ dense** from
  **multi-level trees (worse)**. It is not dense-vs-hub evidence for O1 over an O2 star. It stays evidence against deep
  O2/O3 trees.
- *Overhead percentages (S2):* token overhead inside one task. They do not transfer to operator time, which is the
  scarce resource here. I use none of the percentages.

R3's run is two leads plus vertical parties (the operator above, builders below). As the spec says, I count it for or
against no option on N>2 peer coordination. I use it for two things only: per-hop latency, and the vertical-relay
scenario S-i, which it is direct evidence of.

---

## 1. Scenario × option

Columns:
- **records**: what the store records;
- **woken**: who is woken;
- **operator**: what the operator sees;
- **fails**: the failure modes that appear;
- **hops**: sequential lead turns, and parks where they differ.

"Parks" assumes reviewed rungs unless the scenario says otherwise. A park is one operator act. Script sections are
*§x1*–*§x10*.

### S-a. Three teams; one operator decision all three need

From *§x3* (a table of definitions printed beside the counts, not a computation) and *§x2*.

| | records | woken | operator | fails | hops / answers / relays |
|---|---|---|---|---|---|
| **O1** | 1 decision with `channel=g`. `decisions_readable_by` gains a membership branch | the asker on raise. On answer, all 3, which is new: `store._woken` wakes the asker's session only (R1, relayed) | 1 row in NEEDS YOU | none new. A late joiner reading the decision is a membership question (S-e) | 1 answer, 0 relays |
| **O2-mesh** | 1 decision on pair T1–T2 | T1, T2 | 1 row | T3 needs a relay, which is failure 2 (relayed approval) back again. Or the operator answers the same question twice on two pairs | 1 answer + N−2 relays, or N−1 answers |
| **O2-star** | 1 decision on T1–C, plus a forward per other leaf | T1, C, then each forward's recipient | 1 decision row, plus N−1 parked forwards if C is reviewed | Decided 3 reaches one hop (R1). Without a *forwarded decision* record the relay is C's paraphrase (failure 2). With one, it is a reference, but `decisions_readable_by` must follow forward edges | 1 answer, N−1 relays, 2 hops |
| **O3** | as O2-star | as O2-star | as O2-star | as O2-star. If the root raises it, it still sits on one pair only | as O2-star |
| **O4** | pair-scoped: as O2-star but C fans out once. Gang-scoped: 1 decision readable by the gang | gang-scoped: all | 1 row (+1 parked fan-out if pair-scoped) | gang-scoped decisions need the gang's member set in `decisions_readable_by`. That is an O1 membership set under another name | 1 answer, 0–1 relays |
| **O5** | 1 decision on the shared board | every member | 1 row | readability = board membership. Same as O1 | 1 answer, 0 relays |
| **O6** | not applicable: allocation, not broadcast. Falls back to whatever carries the decision | – | – | – | – |

### S-b. A request from a leaf team to another leaf in a different branch, and its answer

From *§x2* and *§x10* (model; R3's per-hop median 0.61 min and direct round trip 0.71 min; queueing and parks
excluded).

| | records | woken | operator | fails | hops; round trip (median) |
|---|---|---|---|---|---|
| **O1** | 2 messages on the group | **every member, on both sends: 2(N−1) wakes** (*§x2*), unless sends are addressed | 1–2 send parks + 1 declaration on B | bystanders woken for others' traffic, which is the plan's failure 3 inside the bus. With addressed sends the wakes drop to 2, but the record grows a `to` set | 2 hops; 0.71 min |
| **O2-mesh** | 2 pair messages | A, B | as O1 | needs a link per pair: N(N−1)/2 (*§x2*: 66 at N=12) | 2; 0.71 |
| **O2-star** | 4 messages, 2 forward records with provenance | A, C, B, C, A | **up to 5 parks** (A, C, B, C + declaration) (*§x4*) | C must route and keep the thread joined. Answered-for-A is a convention across two records (*§x1*: 12 joint states) | 4; 1.93 |
| **O2/O3-tree** (b=3) | 2d messages, where d = the tree distance | every lead on the path | 2d parks if all are reviewed | as star, per level. N=9: 8 hops; N=27: 12 | N=9: 4.37 min; N=27: 6.81 min |
| **O4** | as O2-mesh, or O2-star (fan-out does not help a 1:1 request) | as underlying | as underlying | as underlying | as underlying |
| **O5** | 1 task written onto B's board with provenance | B (by a board watch; no such wake exists today) | B's declaration park | R1 (relayed): cross-session dependencies "read as missing", and task ids are fleet-wide | 1 write; latency depends on a watcher |
| **O6** | not applicable: the target is known, so this is a plain request | – | – | – | – |

### S-c. Two recipients answer the same needs-answer, or both act on it

From *§x5* (model: each recipient checks "answered?" and then replies).

| | records | woken | operator | fails | count |
|---|---|---|---|---|---|
| **O1** | 1 message, 2+ replies `re` it | all members at once | 2 replies (and up to 2 declaration parks if both act) | **Double answer is the default.** One delivery wakes everyone together, so every check comes before every reply. *§x5*: of the "checks first" interleavings, all are double (k=2: 4/4; k=3: 36/36). Across all interleavings, unguarded, 4/6 (k=2) and 72/90 (k=3). A store-side "first answer wins" removes double *answers* (0) but turns the loser's turn into waste (4/6, 72/90). **Neither stops both *acting*.** That needs a take on the message, as `claim_task` is for a task | 2 turns, 1 wasted at best |
| **O2/O3** | one recipient, chosen by C | C, then the chosen one | 1 | Double action cannot arise from one send. C is the serialiser, so C's latency and its death matter (S-g) | +2 hops |
| **O4** | k pair copies, each answered on its own | k | k replies | **k answers by construction.** A per-copy guard never fires (*§x5*). Fine for "tell everyone"; wrong for "one of you do X" | k |
| **O5** | 1 entry; whoever takes it | all who watch | 1 | needs the same take/claim as O1. The tuple-space "take" is exactly that primitive (R2 Wikipedia, relayed) | 1 |
| **O6** | call-for-proposals, k bids, 1 award | k, then 1 | k bids + 1 award | exactly one actor by protocol. Costs k bids. R2 lists over-bidding and message load (relayed) | 1 + k + 1 messages |

**Revised (S1 finding 5; R5).** ~~"Double answer is the default"~~ is overstated, and the real rate is behavioural.
- *(a) The checks-first figures were not computed.* `section_5` added 1 per interleaving by assumption. R5 replays each
  checks-first interleaving through the same model (`replay`); it prints the same 4/4 and 36/36 (run). That confirms
  the figures are a consequence of the subset's definition: every check precedes every reply. They measure no agent.
- *(b) The subset's premise is an assumption.* `AgentSession.wake` calls `client.query(text)` with no check that the
  session is idle (read, driver.py). The SDK's `query` only writes one JSON user message to the CLI's stdin (read,
  claude_agent_sdk 0.2.167 `client.py`). Whether the CLI interrupts the turn, folds the text into it, or queues it as
  the next turn is CLI behaviour. It is S1 needs-a-run item B, asked of the operator and not run as of
  2026-10-17 (`cmd-20261017T215346Z-89b77062475b`); r5-skeptic-response.md §3 item 1 says what to compare when it is. R3's turn-gap p90 was 9 min, so a woken lead is often mid-turn, and a mid-turn recipient
  checks later than an idle one.

  **Revised (S1 finding 5(b); R7, read of the operator's run).** Item B has now run, once
  (`cmd-20261017T215346Z-89b77062475b`; output quoted in r5-skeptic-response.md, R7). A second `query()` written
  6 s into a turn **was queued as a turn of its own**. Turn 1 ended in `success` at 11.2 s without answering it,
  and `SECOND` followed under a second result at 12.3 s. The flaws: turn 1 made no tool call (the model refused
  the sleep), so this is the mid-*generation* case only. A wake during a tool call, which is the common case for
  a working lead, is untested. It is one sample, on haiku. What it changes here: if wakes queue, a mid-turn
  recipient checks only after its turn ends. When one recipient is idle and another busy, their checks are spread
  apart, and the checks-first subset needs both idle, or both turns ending together. So for the mixed case, double
  action is **less likely than this subset assumed**. It is not shown to be rare. Two idle recipients still
  check together, and (c) still applies. "Possible, rate unknown" stands.
- *(c)* The model assumes a lead checks before acting at all. Nothing measured says it does.

### S-d. A member dies, or pptmstr restarts, mid-thread (Decided 4: held, not dropped)

Common to every option (read, R1 §0 facts 1, 4, 5): the message is in the store and the log before delivery. Today
`_send_wakes` lets go of a wake for an absent session, so held delivery needs a new trigger on resume. ~~Restore needs a
channel-keyed log root. Every option pays this.~~

**Revised (S1 finding 1; R5, run).** Messages restore from their senders' logs with no new root (`r5_probe_a.py`). What
discriminates instead: a held message is in its *sender's* log, so it reaches a resumed recipient only if the sender's
session is resumed too (probe A5). A pair thread needs 2 sessions resumed to be whole; an O1 thread needs every sender.
Only the link record (and decisions) still lack a home.

| | records | woken on resume | operator | fails | difference |
|---|---|---|---|---|---|
| **O1** | per-reader delivered/answered. The dead member's entries stay "held" | that member only | NEEDS YOU at once for that member's owed rows (Decided 4) | other members keep talking (*§x7*: 0 of 28 pairs cut). The group thread moves on without the dead member. On resume it reads a backlog it was not part of. A needs-answer *to the group* looks unanswered by one member even if another answered (*§x1*: any/all ambiguity, 6 of 16 joint states at N=3) | per-reader restore. R1: L if membership changes |
| **O2/O3** | per pair | that member | per pair | if the dead member is a leaf, 0 pairs cut. If it is a coordinator, see S-g. A forward whose source channel's log is missing on restore is a dangling reference (R1, read) | – |
| **O4** | K copies; each copy held independently | that member | per copy | a gang send is partly delivered: the "answered for the send" aggregate stays open | – |
| **O5** | state persists by construction | – | – | nobody is woken by a write, so "held" means unread | – |
| **O6** | an open call | – | – | a dead bidder simply does not bid. A dead awardee holds the task: it needs an award timeout | – |

### S-e. A fourth team joins mid-effort; a team leaves

From *§x8* (definitions, not computation).

| | records | operator acts | fails |
|---|---|---|---|
| **O1** | `MemberJoined`/`MemberLeft`; a recipient set frozen on each message (R1) | 1 per join or leave | **Does the joiner read history?** That decides what it can see from before it joined. A leaver's owed needs-answers must be released. R1 sizes O1 at L once membership changes. Fixed membership is M |
| **O2-mesh** | N links | N per join | links grow quadratically. Each link has its own charter for the operator to write |
| **O2-star/tree** | 1 link | 1 | a joining coordinator, or a leaf that has to sit under a different coordinator, is a re-link |
| **O3** | 1 directed link | 1, plus choosing a parent | a second parent is refused |
| **O4** | 1 link + an edit to the gang | 2 | what a gang means once one of its channels is unlinked is unsettled (R1) |
| **O5** | 1 membership grant | 1 | history is visible by default |
| **O6** | nothing; the next call includes it | 0 | none |

### S-f. Two existing groups are ganged together (sizes 4 and 5)

| | operator acts | fails |
|---|---|---|
| **O1** | 1 new channel of 9, or 1 bridge channel of 2 representatives | a channel of 9 hits S-c and S-j harder (wakes 72 per reply-all round, *§x9* scaled). A bridge is O2 inside O1 |
| **O2-mesh** | 20 links | none, apart from operator effort |
| **O2-star/tree** | 1 link between the two coordinators, or 2 under a new one | two coordinators linked as peers is fine under O2 |
| **O3** | 2 links under a new root. **Linking the two coordinators as peers is a cross-link and is refused** | forces a new level. *§x10*: every cross-group exchange then pays 2 more hops each way |
| **O4** | 1 gang record over existing links | fan-out across the gang. The gang's decision scope needs a member set (S-a) |
| **O5** | merge the boards, or cross-reference them | what a merged board means for two operators' rungs is unclear |
| **O6** | none: a call goes to whoever the caller can address, so ganging is whatever addressing O6 rides on | inherits that option's gang cost |

### S-g. The coordinator itself dies, or is slow (O2/O3)

From *§x7*, N=9 (model).

| | team pairs cut off while it is down | fails |
|---|---|---|
| **O2-star**, C down | **36 of 36** | everything held (Decided 4) until C resumes. NEEDS YOU surfaces each needs-answer *to C* at once. Messages A wants to reach B were never sent, because they would go via C, so nothing surfaces for them |
| **O3-tree** (b=3), root down | 27 of 36 | only same-branch pairs still talk, and only through their mid coordinator |
| **O3-tree**, a mid coordinator down | 21 of 36 | – |
| **O2-mesh / O1 / O4-mesh**, any lead down | 0 | only that lead's own threads wait |

**Revised (S1 finding 9; R5, read).** "36 of 36" is the model's count and it stands, but it reads as worse than it is.
In one window C's death is a session event, Decided 4 surfaces every needs-answer to C at once, and the operator can
link A–B directly. What stays: A's messages to B that would go via C were never sent, so nothing surfaces for them.
The whole process is a shared single point of failure for every option. Costs nobody counted, for O2: C's context
accumulates every thread it routes and pptmstr compacts contexts (`CompactionObserved`), so C can lose thread state; C's
tokens per hop; queueing at a busy C. For O1: every member's context grows with others' traffic. None is measured.

*Slow* coordinator: R3's turn-gap p90 was 8.98 min and the maximum 18.48 min (run, re-run). A coordinator serving several
threads queues them. R3's one-hop figure (0.61 min) excludes queueing, and no data here measures it. On a reviewed rung,
each forward also waits on the operator (*§x4*: +2 parks per exchange).

### S-h. Mixed rungs across three or more teams (Decided 1)

From *§x4*: every rung assignment for request + declaration + answer.
- **Direct (O1, O2-mesh, O4):** parks = [A] + 2[B]. The range is 0–3, and depends only on the two ends.
- **Through a coordinator (O2-star, O3):** parks = [A] + 2[C] + 2[B]. The range is 0–5. **A reviewed coordinator adds 2
  parks to every exchange it carries, whatever A's and B's rungs are.**
- In O2/O3, two autonomous teams talking through a reviewed coordinator therefore park twice, on a board neither of them
  owns. Decided 1 holds ("the rung belongs to the board it protects"). But the coordinator's rung now governs *routing*
  for others, which is the coupling Decided 1 rejected for gating, coming back through topology (inferred).
- O1: one send parks once and reaches N−1. It cannot be *partly* gated. A reviewed sender's message is either out to
  everyone or to no one.
- O4: one fan-out parks once and reaches K.
- O5: a write onto another team's board is a declaration, so it parks on that board's rung, as the direct row does;
  the writer's rung gates nothing unless the write is also a gated tool (not costed by R1).
- O6: a call parks once on the caller's rung, each bid on its bidder's rung, and the award's declaration on the
  winner's board. That is 1 + k + 1 possible parks for one allocation (inferred; not in *§x4*).
- S-g applies to O2/O3 only, as the spec scopes it. Its last row covers the options with no coordinator.
- Decided 2 (charter judged at the parked declaration, or self-reported on AUTONOMOUS) is unchanged in every option: the
  declaration is on the receiving board.
- The charter is per channel. In O1 one charter covers N members, so "what each side may ask of the other" is now N
  sides. In O3 it flows down only (R1: this strains proposal item 1).

### Added scenarios

**S-i. The vertical relay** (from R3: 37 of 42 messages carried a third party, every one of them above both leads or below
one). Neither the operator's relayed answers nor builders' relayed results are peer traffic. They are handled by
Decided 3 and proposal item 7, and those work the same across options **within one channel**. They differ once a third
team needs the same answer: that case is S-a. So the vertical relay favours whichever option makes Decided 3 reach every
team that needs it: O1, gang-scoped O4, O5. It does not tell a group from a coordinator by itself (inferred).

**S-j. A reply-all round, and a thread with no termination** (from R2 S18, AutoGen's "limit the conversation to 25 messages
to avoid infinite loop" (fetched), and MAST FM-1.5 "unaware of termination conditions", 12.4% (relayed; **Revised (R6; R7): run**. R6 matched 12.4% in
the §4 body, and I re-grepped it: "not recognizing task completion (FM-1.5, 12.4%)". The quoted name is the
taxonomy's label for FM-1.5)). From *§x9*: one
round where every member answers once is N(N−1) wakes in O1 and O4-mesh (42 at N=7), and N² in O2-star.
`WAKE_WINDOW_S` is 2 s (read). R3's turns were 0.4–1.9 min, so coalescing rarely merges these. Each wake is a turn.
- O1 has no turn-taking manager, and S18 says mature group chats need one.
- O2's coordinator *is* one, by convention.
- No option has a loop cap today. `_concern_wake`'s rule exists precisely to prevent wake loops (read), and every option
  breaks it on purpose.

**S-k. Forwarding loops on symmetric links** (from R1's invariant table, "the operator can link a cycle", made mechanical).
From *§x6*, where every lead forwards whatever it receives:

| graph | naive forward | provenance chain | per-lead seen-set |
|---|---|---|---|
| tree / star | = links | = links | = links |
| triangle | ≥10 000 (unbounded) | 4 | 4 |
| mesh of 7 | unbounded | 1956 | 36 |

- A provenance chain alone stops the loop, but copies grow with the number of simple paths.
- A store-side rule, "a lead forwards a given original id at most once", bounds copies at 2·links − (leads − 1). The
  script asserts this formula holds on every graph it prints.
- O3's enforced tree makes every rule equivalent.
- O2 needs the seen-set rule, or the operator never linking a cycle. The rule is a small store check on the forward
  intent (inferred).

**S-l. Telephone and withholding through a relay** (from R2 S10, "game of telephone" (relayed); S17 (relayed); MAST FM-2.4
"information withholding", 0.85% (relayed)). O2/O3 forward *by reference*, so the body is not re-typed. The coordinator
still chooses *whether* and *to whom* to forward, and withholding is that choice. O1, O4 and O5 have no intermediary to
withhold. The measured rate (0.85% within single-task systems) is small, and its transfer is unknown.

**Revised (R6; R7).** The FM-2.4 tag is now **run**, with a caveat. R6 found 0.85% in MAST's §4 body text,
"withholding crucial information (FM-2.4, 0.85%)", but Figure 1 prints "(0.80%)" for the same mode. I re-grepped
R6's extracted text and found both (run). The paper disagrees with itself, so 0.85% is the body's value, not
the only one. Either value is small, and nothing here moves.

---

## 2. Comparison matrix

Cost is from R1 (read, relayed by R1 as inference for the sizes). The **shared core is L for every option**: a
channel-keyed log root and its restore, cross-session routing in the store, a cross-session wake, held delivery on
resume, a timer for Decided 4, and the unlogged-decisions conflict. None of that discriminates between the options.

**Revised (S1 findings 1, 3, 4, 7, 10; R5).** The matrix is kept as written; read these cells against it:
- *Shared core.* Messages need no channel-keyed root (run, finding 1). The root is needed for the link record and for
  decisions. And it now discriminates: a single-recipient record (pairs, O4 copies, addressed O1 sends) is close to
  `Concern`; an unaddressed multi-recipient O1 record is a new kind, and a thread over N senders' logs is whole only
  when all N are resumed.
- *O1 failure modes.* "any/all ambiguity growing as 4^(N−1)" dissolves under per-reader "answered", which the lean
  itself adopts (*§x1* overcounts O1). "Double answer on simultaneous wake" is a model consequence, not a measured rate.
- *O1 evidence against.* "S2 decentralized 7.8×" is withdrawn (finding 3).
- *O2 failure modes.* "+2 parks" only on a reviewed, working coordinator (finding 4). *§x9*'s N² for O2-star assumes C
  forwards every reply to everyone. A turn-taking C that aggregates is about 2N (overcounts O2).
- *O2/O3 evidence.* "S5 tree worst" holds for deep trees, not a one-level star. "S4 layered lowest" is withdrawn: Layer's
  wiring is not stated (finding 7). "S1 DAG→cyclic" is withdrawn: it is a supervised review loop (finding 8).
  **Revised (R6; R7):** the cell's "(relayed)" figure now matches the PDF (run, R6; re-grepped by R7). The
  withdrawal stands, because it rests on the figure's meaning and not on its accuracy.
- *Latency.* *§x10* uses the farthest pair (worst case) for trees against a constant for O1, which is against O2/O3.

| | O1 group | O2 coordinator (convention) | O3 enforced tree | O4 fan-out | O5 shared board | O6 contract net |
|---|---|---|---|---|---|---|
| **Failure modes (mechanical)** | double answer on simultaneous wake (*§x5*); any/all ambiguity growing as 4^(N−1) joint states (*§x1*); bystander wakes, 2(N−1) per leaf→leaf exchange (*§x2*); reply-all N(N−1) (*§x9*) | cut-off when C is down (36/36 at N=9, *§x7*); +2 parks per exchange on a reviewed C (*§x4*); forwarding loops without a seen-set (*§x6*); Decided 3 reaches one hop (S-a) | as O2, minus loops; plus a refused peer-gang (S-f) and depth-driven hops (*§x10*) | k answers by construction (*§x5*); the aggregate "answered" has O1's any/all question (*§x1*) | needs a take primitive, as O1 does; no wake on write today | k bids per allocation; an award timeout is needed |
| **Failure modes (evidence, transfer stated)** | S7 conformity (needs a visible majority, so it applies only here; size unknown); S2 decentralized 7.8× (1–4 agents, no gate: weak) | S10 hub blocks on its slowest (C); S1 star "potentially" causes premature termination (hedged by the source); S5 tree worst (task-specific, weak) | S1 DAG→cyclic improved ChatDev (relayed); S4 layered lowest (weak); S15 Erlang does not enforce messaging hierarchy (design) | S17 parallel recipients' conflicting implicit decisions (opinion); S2 independent 17.2× (no gate: weak) | S9, a single paper (weak) | no LLM evidence |
| **Cost: differing part** (R1) | M fixed membership / **L** with join/leave | M (forward record + tool; readability follows forward edges) | S on top of O2 | S–M (gang record, `send_id`, fan-out arm) | not costed; R1 flags fleet-wide task ids and cross-session dependencies | not costed |
| **Format freezes** (R1; "nothing is renamed, retyped or removed", read) | a member set and per-reader receipt kinds | a forward record and the chain's shape; a *pair* channel shape that cannot later be retyped to a set | a direction field (additive) | a gang record and `send_id` | cross-session task fields | call/bid/award kinds |
| **What the operator does** | 1 link per group; 1 park reaches N−1; 1 decision answer for all | builds the tree; reviews every forward on a reviewed C; answers once, then forwards are relayed | as O2, plus picks parents | builds links (quadratic on a mesh) and gangs; 1 park per fan-out | grants membership | reviews awards (if gated) |
| **Composability** | join/leave is the hard part (history, owed answers); a gang is a bigger group or a bridge | join = 1 link; gang = 1 link between coordinators | gang forces a new root | gang = 1 record; join = link + gang edit | merging boards is unclear | joins for free |
| **Latency** (*§x10*, model) | 0.71 min median request→answer | star 1.93; tree N=9 4.37; N=27 6.81, plus unmeasured queueing and +2 operator waits per reviewed C | as O2-tree | as underlying links | depends on a watcher | 3 messages before work starts |

---

## 3. Judgement (mine; not evidence)

**The lead's O2 hypothesis does not survive as the default. It does survive as something the operator can build on
top.**

1. **Decided 3 decides more than it looks.** The operator ratified channel-readable decisions "if this makes N>2 team
   coordination easier". Under O2/O3 it reaches one hop. Every further team gets a relay, which is failure 2, or a new
   forwarded-decision record (S-a). The one failure the 2026-10-17 run actually had in bulk was relayed authority (R3:
   13 relayed in, 5 requests resting on a relayed yes). An option that brings it back for every third team works against
   the reason Decided 3 exists. O1, gang-scoped O4 and O5 do not bring it back.

   **Revised (S1 findings 2 and 11; R5).** Weakened, not withdrawn.
   - *The weight is two-party.* R3's 13 relays and 5 requests on a relayed yes all crossed one
     pair, which Decided 3 fixes under every option. How often a third team needs the same
     decision is unknown (§4 item 2). The argument weights a scenario nobody observed (read, R3).
   - *The discrimination is in Decided 3's scope, not in pairs.* Decided 3 as written ("can be read
     by every lead on the channel") is silent on a lead on several channels. Under a rule "a
     decision is readable on every channel its asker is a member of", a decision the coordinator
     raises is read directly by every leaf: 0 relayed *answers*. A leaf's question still reaches C
     first, but that is a relayed question, and failure 2 was relayed *authority* (inferred). That
     rule is the team's to settle, and it flips S-a for O2-star.
   - *S-a contradicted itself.* `r4_explore.py` §x3 said "0 relays if the root raises" and the S-a
     table said a root-raised decision "still sits on one pair only". The table is right under
     Decided 3 as written. The script row is corrected (run; the only output line that changed).
   - *The advantage does not survive a restart today.* Decisions are not logged (`model.Decision`
     docstring: "Not in the board log. A decision does not survive a restart of pptmstr";
     `boardfacts.outcomes` returns `()` for all five decision intents; read). An O1
     membership-read decision vanishes on restart; an O2 relay is a logged message and survives.
     `boardlog.py` already reserves "a decision raised and answered" as an expected kind, so
     logging decisions is a prerequisite for Decided 3 under *every* option.
2. **On reviewed rungs, the coordinator's main evidential advantage is already provided by the gate.** S2's hub contains
   errors "by enforcing validation bottlenecks" (fetched). pptmstr's validation is the operator at the sender's park and
   at the receiving board's declaration (Decided 1, proposal item 3). That exists without a hub. What the hub adds on
   reviewed rungs is +2 parks per exchange (*§x4*), and operator attention is the scarcest thing in this design. On
   AUTONOMOUS, a coordinator lead is the only validator available, so the hub evidence transfers there.

   **Revised (S1 findings 3 and 4; R5).** The conclusion survives, with a different reason.
   ~~On reviewed rungs, S2's mechanism is present without a hub.~~ S2's orchestrator "cross-checks reasoning steps
   before aggregation", with "detection and correction of logical inconsistencies", "through supervised aggregation"
   (fetched, both halves of the HTML). No human appears. The operator's gate is a content review of one send at a
   time. It sees the text, and it exists because a message "changes what another agent does next" (`approval.py`
   `_REVIEW` comment, read). It does not cross-check several sources or aggregate, and it is absent on AUTONOMOUS.
   So the gate is not S2's mechanism either. The point that stands: **O2's coordinator, forwarding by reference,
   is not S2's mechanism on any rung.** S2 (and S6's Inspector) are weighed for no option as specified.
   ~~On AUTONOMOUS … the hub evidence transfers there.~~ Withdrawn: it transfers only to a coordinator that checks and
   aggregates, which O2 does not specify.
   *+2 parks* holds only for a reviewed C (§x4 prints C=0 too: the same parks as direct). Putting C on AUTONOMOUS
   avoids them, but `_AUTONOMOUS_AUTO = _REVIEW` releases C's whole review list, its own `Edit` and `Bash` included
   (read). That is free only for a coordinator that does no work of its own. So +2 is a cost of a *working,
   reviewed* coordinator, not of O2 as such.
3. **O3's enforcement buys loop-freedom, and a per-lead seen-set buys the same for O2 at a bounded cost** (*§x6*). After
   Axis A, O3 has no other support. It has a concrete cost in S-f (a refused peer-gang) and S-g. I would not build O3.
4. **O1's real problems are mechanical and nameable: double action on a simultaneous wake, the any/all question, and
   bystander wakes.** Each has a known remedy from this tree:
   - a *take* on a needs-answer, as `claim_task` is for a task;
   - an optional `to` subset on a send, which bounds who is woken and who owes;
   - "answered" derived per owed reader rather than for the group.

   The remedies also apply to O4's aggregate, which has the identical state space (*§x1*). O4 relocates the problem
   rather than removing it.
5. **The format argument (read).** The log format "only grows; nothing is renamed, retyped or removed". If the channel
   record is born as a pair, `(a, b)`, then N>2 later means a second record kind beside it forever. A channel whose
   members are a set, created with exactly two members, keeps the pair as the common case and leaves N>2 as data, not as
   a format change. This is the strongest reason to decide the record shape now, even if N>2 behaviour ships later.

   **Revised (S1 finding 13; R5, read).** Right, with a sharper reason, and **not evidence for O1**. The same
   docstring says "a build older than a field ignores the key", while an unknown *kind* reads as `Unrecognised` and
   makes the board short. So widening a pair record by a field later would be silently misread by an older build as
   a pair; only a new kind is noticed. A member-set link record admits O2, O3 and O4 equally (a pair is a set of two),
   so it is a no-regret choice under every option. Per finding 1, where that record lives (it fits no single
   session's log) has to be settled with it.

**Where it leans:**
- one channel record with a member set (a pair is a set of two);
- **membership fixed at link time** (R1's M, not L). Joining means re-linking a new channel, which sidesteps the history
  question until a run shows it is needed;
- channel decisions readable by all members (Decided 3 as written);
- sends optionally addressed (`to` ⊆ members) so that waking and owing follow the address;
- a store-side take on a needs-answer, so that one recipient acts;
- **coordinator leads stay possible by convention.** A holon is a lead that is a member of two channels. A
  forward-by-reference with a seen-set rule is an S add-on if a run shows it is wanted.

This is O1's record carrying O2's convention. It is not O1 as "N leads talk freely": S18 says nobody ships that without a
turn-taking manager and a cap, and the take and the `to` are that manager in store form.

**Revised (S1 findings 1, 5, 6; R5).** ~~"O1-shaped"~~ is stronger than the evidence. With `to` on every needs-answer
and "answered" derived per owed reader, an O1 send is K single-reader records sharing a send, which is O4's shape
(§x1 already says the state space is identical). Finding 1 (run) adds that single-reader records are the ones the
existing per-sender log already carries. What stays distinctive, and what the evidence supports, is:
- a **member-set link record** (no-regret under every option; finding 13);
- **decisions readable by membership** (needs decisions logged first; finding 11);
- **one link act for N leads**;
- **addressed messages**, filed per sender as single-recipient records.

The everyone-sees-everything group thread is exactly what the remedies take out. A design nobody walked, from S1:
**split by kind.** The group carries decisions and non-actionable status; a needs-answer must be addressed to one
member. That gets S-a's benefit without depending on the untested store-side take, which then matters only for
"anyone, take this". The lean is restated in r5-skeptic-response.md §2.

**What would reverse it:**
- **A three-team run in which addressed sends and the take do not stop double action or reply storms**, for example
  leads ignoring `to`, or acting before taking. Then the group thread is the problem, and a coordinator that serialises
  (O2) earns its hops.
- **Evidence that N>2 traffic is almost all vertical or client–supplier** (R3's shape, repeated in runs with a real third
  team), so that peers rarely need the same decision. Then S-a is rare, O2's one-hop limit costs little, and pairs plus
  a coordinator are simpler to reason about.
- **Operators finding the coordinator's parks valuable rather than costly**, as an inspection point in the sense of S6's
  Inspector. Then +2 parks is a feature.
- **A measured conformity effect (S7) between leads on a shared thread.** That would be against O1 specifically.
- ~~**Mostly AUTONOMOUS fleets.** There the gate validates nothing, and S2's hub result transfers best.~~
  **Revised (S1 finding 3; R5).** Withdrawn as stated: S2's result transfers only to a coordinator that checks and
  aggregates, which O2 does not specify. It becomes a reversal condition only if a *validating* coordinator is
  proposed and costed, on AUTONOMOUS fleets.

---

## 4. Open unknowns, and what a cheap experiment would settle

1. **Do leads double-act on one needs-answer when woken together?** *§x5* says the interleavings allow it every time.
   Whether a model checks before acting is behavioural.
   *Experiment:* three sessions in one window on a hand-built group of three (even through files, as on 2026-10-17). Send
   10 "one of you do X" messages. Count double declarations with and without an instruction to take first.
2. **How much N>2 traffic is peer, and how much is vertical?** R3 has only vertical.
   *Experiment:* the same three-team run, classified with R3's script extended to three folders. Count messages a second
   peer also needed: that is S-a's frequency.
3. **The coordinator's queueing delay**, unmeasured. R3's turn-gap p90 of 9 min suggests it is not small.
   *Experiment:* a star of one coordinator and three leaves on the same task. Measure forward latency against the
   coordinator's own work.
4. **The operator's response time to a park.** It multiplies every count in *§x4*. R3 had no cross-boundary park.
   *Experiment:* any reviewed-rung run of the channel, once built.
5. **Does a joiner need history?** This decides O1 at M or L.
   *Experiment:* in the three-team run, add a fourth team after an hour and record what it asks for.
6. **Whether channel decisions must persist** (R1 fact 6: decisions are not logged, and proposal item 5 wants the thread
   to survive). This is not topology-specific, but O1 makes it bite harder: one decision serves N members.
7. **Whether a channel log can be restored without a session resume driving it** (R1 §4.1). This is shared core, and
   unknown.
8. **R3's classification** has one reader. Nothing here rests on its class counts, only on the party positions, which S-i
   uses.

## What was run and what was not

- **Run:**
  - `python3 -I r4_explore.py` (twice, identical output; `black`, and `mypy --strict` with the main checkout's venv);
  - R3's `r3_count.py` on the two folders (figures as R3 reported).
- **Fetched:** the S2, S18 and S1 quotes named under "How to read this".
- **Not run:**
  - any agent behaviour: every *§x* count is arithmetic over a stated model;
  - the latency figures, which exclude queueing and park times;
  - *§x3* and *§x8*, which are definition tables printed beside the counts, not computations;
  - no code under `pptmstr/` was written or executed.
- **Not checked:**
  - R2's quotes other than those three sources;
  - R1's sizes (S/M/L), which are R1's judgement;
  - R3's per-message classification.
