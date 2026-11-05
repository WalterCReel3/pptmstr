# Two leads in one window share a channel

**Dated:** 2026-10-17 · **Status:** scope for work not started; nothing boarded. The operator:
"write up this planning but do not board anything. I'm going to start a new session for this."
· **Builds on:** [`2026-10-12-wiring-the-inter-team-record.md`](2026-10-12-wiring-the-inter-team-record.md)
(the request channel left unbuilt, decision 4 on delivery) and
[`2026-10-16-an-answer-wakes-its-asker.md`](2026-10-16-an-answer-wakes-its-asker.md) (the wake path).

## Why

On 2026-10-17 the ui-rework lead produced the screenshots, loops and crops for the holond-brand
session's product page (v1–v4). The two leads had no channel between them, so they talked through
files. Each wrote only in its own tree and polled the other's with
`scripts/watch_dir.sh`. The rules were `shots/lander/PROTOCOL.md`, improvised by the ui-rework
lead. The operator then asked to "codify that feature into pptmstr in a way that would be
seamless … more pptmstr MCP features that allow addressability between leads. The file system
coordination could still be the delivery functions but the awareness, triggers and checks happen
on the bus."

## What the file protocol did (counted from the two folders; run)

- 35 messages in about three hours: 21 from the pptmstr lead, 14 from the brand lead. Replies
  usually arrived 1–3 minutes after the other side wrote. No message was lost. Neither side wrote
  into the other's tree.
- Real decisions crossed the boundary: the "cost" copy was rewritten after a run showed it false;
  the master size changed to 1572×900; the spotlight mask was settled.

**Corrected by measurement** ([R3](2026-10-17-channel-topology/r3-run-evidence.md), `r3_count.py`, file mtimes
as the clock, re-run by the gate). The counts above were taken partway through the run:
- **Messages:** there were 41 (23 pptmstr, 18 brand) over 233 min.
- **Replies:** not counting acknowledgements, a needs-answer was answered in a median of 0.84 min (p90 25.9 min), and
  one (`T1350`) was never answered.
- **Crossings:** messages crossed once, not twice.
- **Typed timestamps:** pptmstr's typed names were wrong in both directions (−127 to +122 min). Even the brand side's
  names put two of its messages in the wrong order.
- **Who else was in the conversation:** every third party was either above both leads (the operator, whom the brand
  lead called "founder") or below one lead (its builders). There was never a peer third team.

**What went wrong, worst first:**
1. **Timestamps an agent typed were wrong.** The pptmstr lead's file names ran up to 2 h ahead of
   the real clock (`T1700` written at 15:04); the brand lead's were right. Ordering by name
   interleaved the two logs wrongly, and twice messages crossed without either side seeing it.
2. **Scope grew through approvals nobody could verify.** v2, v3 and v4 each arrived as "the founder
   said yes (relayed)". The pptmstr lead accepted each one; the only check was that the operator
   was present in that session and could have objected.
3. **Waking was manual and noisy.** About 20 re-arms of a polling script. Three false wakes came
   from the brand session's own builder results before a name filter was added.
4. **No receipts.** A message was known to be read only when it was answered.
5. **The lead was a relay.** Each brand request was copied by hand into a builder concern.
6. **No view of the channel.** "Is the other side alive?" and "what is still open between us?" had
   no answer except the lead's memory.

## The operator's constraints

- "I did have both teams in the same process/window. I think that's the easiest, UX-wise, so
  inter-process is a less important consideration."
- "The charter is gated unless, of course, we're in autonomous mode."

## The proposal

A channel between two leads, held in the store of the one window. That's the same place concerns
and decisions live, so it needs no files, no watcher and no on-disk format of its own.

1. **Linking is the operator's act.** Select two session cards and choose Session ▸ Link leads, or
   name an existing session in the launcher. Linking asks for a **channel name** and a short
   **charter** (what each side may ask of the other). No agent can link itself, and none can see
   a session it isn't linked to: discovery is the link.
2. **Bus tools:**
   - `send_to_peer(channel, subject, body, needs_answer, re)`;
   - `read_peer_messages(channel)`.

   **The store stamps** ordering, wall-clock time, sender identity and read receipts, so nothing
   an agent types decides them (failure 1). **Peer text is quoted** like any agent's text (`| `),
   so it is never read as an instruction from pptmstr or the operator. **Delivery wakes the
   recipient** through the existing wake path (`AgentSession.wake`, driver.py ~l.3066;
   `app._send_wakes`, app.py ~l.1124; read), so nothing is re-armed (failure 3).
3. **The gate follows the rung, with no new approval mechanism.**
   - On a reviewed rung (STRICT, PERMISSIVE), a peer message parks as `post_concern` does.
   - Work a peer asks for enters the recipient's board through `declare_task`, which already
     parks on those rungs. The parked row now carries its **provenance**: "requested by
     <peer> on channel <name>, re: <message>", and whether the request falls **inside the
     charter**.
   - On AUTONOMOUS both flow, as `approval._AUTONOMOUS_AUTO` already lets the bus calls through
     (approval.py ~l.233; read).
4. **A relayed approval becomes a decision.** Both leads are in the operator's window, so "the
   founder said yes (relayed)" doesn't need relaying. The asking lead raises a decision on the
   channel, the operator answers it once in NEEDS YOU, and **both** leads can read the answer
   with `read_decisions` (failure 2). This changes `Snapshot.decisions_readable_by` (2026-10-12,
   Q4: "the answer goes to the asker; the session root may read it").
5. **The thread survives a restart.** Channel records go into the board log with the rest of the
   board (`boardsync`, `boardfacts`). The operator restarted pptmstr mid-session on 2026-10-17.
6. **Visibility:**
   - each linked card shows its channel and the count of unanswered needs-answer messages
     (failures 4 and 6);
   - the thread is readable in DIGEST;
   - a needs-answer left unanswered past some age (not decided) surfaces in NEEDS YOU.
7. **The lead stops being a relay.** A builder can be handed a channel message by reference, as
   it is handed a task, and read it whole. Authority still comes from the lead: only the lead
   sends, and work still enters through a declaration (failure 5).

## Separate and unchanged: a deliverables exchange

The product page links by relative path into a gitignored folder of the ui-rework worktree
(`shots/lander/`). If that worktree is cleaned up, the page breaks.

- `publish(name, paths, manifest)` hard-links or copies into
  `~/.config/pptmstr/exchange/<channel>/<name>/vN/`, with a content hash, the recipe and the claims
  each file makes.
- `read_deliverable(name, version)` gives the recipient a stable path and the version history.

Neither side writes into the other's tree. This fixes the fragility directly, and it's useful
with or without the channel.

## Deferred, with the reason

- **A cross-process channel**, wiring `pptmstr/requests.py`: the operator works in one window, so
  inter-process is "a less important consideration". Its blockers from 2026-10-12 still stand: an
  unversioned record digest, and the Q3 address scheme. If it is built later, channel names
  (operator-given) should be the addresses, not session ids, so a resumed session keeps its
  channel.
- **A general `watch(source, filter)` on the bus:** the only case that mattered today, a peer
  message, is covered by the wake on delivery.

## Decided

1. **Mixed rungs: each side's own rung governs** (operator, 2026-10-17). Sending is gated by the
   sender's rung; work landing on a board is gated by that board's rung. So an autonomous brand
   lead can ask freely, but its requests still park on a reviewed pptmstr lead. The rejected
   alternative was letting the stricter rung govern both sides. That would make a session's gating
   depend on who it is linked to, so it could no longer be read from its own card. The rung belongs
   to the board it protects.
2. **Who judges the charter depends on the rung** (operator, 2026-10-17: "the behavior you
   outlined is the ideal"). On a reviewed rung the operator judges at the parked row, and the row
   shows the charter next to the request. On AUTONOMOUS nothing parks, so the receiving lead marks
   its own declaration inside or outside the charter. The board shows that mark as self-reported,
   as it shows `basis`. pptmstr doesn't judge it: the charter is free text.
3. **A decision raised on a channel can be read by every lead on the channel** (operator,
   2026-10-17: "if this makes N>2 team coordination easier then yes"). It does: no member has to be
   named as a reader, and no answer has to be relayed. This applies only to decisions that carry a
   channel. Ordinary decisions keep the current rule in `Snapshot.decisions_readable_by`.
4. **A needs-answer surfaces in NEEDS YOU after a fixed age**, set in the one settings place
   (default 15 min; the 2026-10-17 replies took 1–3 min). It surfaces at once if the recipient
   session is dead or ended. A turn that ends without a reply is not a trigger: a lead may
   legitimately wait on its builder first. The operator added: "we should try not to drop
   messages." So a message is in the store and the board log before any delivery is tried. A
   message for a session that isn't running is held and handed over when that session resumes,
   not thrown away. NEEDS YOU is the backstop, not the means of delivery.

   **Revised by Decided 6.** "Handed over when that session resumes" assumed the recipient's
   resume was enough. As built, it isn't: the message lives in the sender's log.
5. **The record layer is the same under every topology** (operator, 2026-10-17: "yes").
   - The link record holds a *set* of member sessions, and a pair is a set of two.
   - Decisions are written to the log.
   - A message is addressed to one reader and filed in its sender's log. A send to several readers
     is several such records sharing a send id.

   The reason, from [R4 Judgement 5 and R5 finding 13](2026-10-17-channel-topology/r5-skeptic-response.md):
   the log format only grows, and an older build ignores a field it doesn't know. A record created
   as a pair and widened later would be silently misread as a pair, so the set has to be in the
   first version. The topology is then a rule about what a send may address, and changing it is
   not a format change.
6. **A held message is safe on disk and is delivered when its *sender's* session resumes**
   (operator, 2026-10-17: "The rx'er has no responsibility to the sender"). This is what the tree
   already does (`r5_probe_a.py`, run).
   - A message is filed in the sender's log.
   - Resuming the sender brings it back into the store, delivered or waiting, whether or not the
     recipient is running at that moment. Once the recipient is running, the message is in its
     inbox.
   - Resuming only the recipient doesn't surface it, and nothing obliges the recipient to know
     about it.
   - A needs-answer becomes the recipient's obligation at delivery, not at sending. Decided 4's
     age therefore runs from delivery.
   - The operator learns that a peer isn't running from the card's channel line (proposal item 6).
     That is visibility for the operator, not a duty for the recipient.
   - The rejected alternatives were reading linked peers' logs on the recipient's resume, writing
     each message into both logs, and keeping messages in a channel log. Each costs a new restore
     path or a new record kind, to give the recipient a responsibility the operator ruled it
     doesn't have.
7. **Pairs only, to start** (operator, 2026-10-17: "Pairs only to start. If the record is flexible
   in the future then that's a workable start"). This settles Q5 for the first build.
   - The link record holds a member set (Decided 5), and linking refuses any set that isn't
     exactly two members.
   - N>2 is reached through a lead that is a member of several pair channels, which is a
     coordinator by convention.
   - Relaxing the two-member rule later is a policy change, not a format change.
   - The topology research ([`2026-10-17-channel-topology/`](2026-10-17-channel-topology/)) found no
     evidence that separates the options. The three-team run in its r5 §3 is what should come
     before any relaxation.
8. **H+: a peer message is a `Concern` in the store, sent and read with its own tools, and filed in
   the log under its own record kind, `peer-posted`** (operator, 2026-10-18: "Let's go with H+").
   The reasoning and every count are in
   [`2026-10-18-peer-message-shape/m2-judgement.md`](2026-10-18-peer-message-shape/m2-judgement.md),
   §(d) and "Revised (S5; M3)".
   - **The store.** `Concern` gains `channel`, `needs_answer` and `re`. Peer-ness is derived from
     `channel is not None` and never stored as a second flag. `inbox_of` and a new `peer_inbox_of`
     split `snap.concerns` on that predicate, and a test pins that the two halves are disjoint and
     cover every waiting concern. On a peer message, `task_id` names the **sender's** task.
   - **The tools.** `send_to_peer` is in `_REVIEW`. `read_peer_messages` is in `_BUS_AUTO`, like
     `read_inbox` (corrected 2026-10-18 from "both in `_REVIEW`"). Gating a read approves the call
     before its content exists, so it would show the operator nothing. By Decided 1 the sender's
     rung governs what is sent, so a reviewed-rung reader can receive text from an AUTONOMOUS
     sender that no operator saw. Channel names never
     appear in `post_concern`'s `to`, so roles and channels don't share a namespace.
   - **The log.** `boardfacts._filed` writes a concern with a channel as `peer-posted`. An older
     build, and every build on this machine that shares `~/.config/pptmstr` is one, reads it as
     `Unrecognised` and marks the board short; it doesn't misfile it as team mail.
   - **Acceptance criteria. Each ships in the same change as the first code that writes
     `peer-posted`, not after it:**
     1. **The kind pin.** The *encoded* kind of a concern with a channel is `peer-posted`, and is
        not in a frozen list of the kinds a pre-peer build decodes (m2-judgement.md, "H+: the pin
        for `_filed`"). It is the only check that catches a widened `concern-posted` (run, M3 C).
     2. **The end-to-end round trip.** `ConcernPosted` → outcomes → encode/decode → restore →
        `BoardRestored` → the store, asserting the channel survives. It is the only check that
        catches `store._restored_concern` dropping `channel` (run, M3 C/E). The `_concern`
        generator in `tests/test_boardfacts.py` must set `channel`, or that test can't see it.
     3. **The inbox split pin** described above.
   - **Why not P.** H+ and P2 tie on silent hazards (H+ 3–4, P2 2–3, depending on the cases
     below). With the kind pin in H+'s spec, tie-break (ii) picks H+: fewer duplicated structures,
     ~31 sites against ~45. Moving H+ → P later is a store refactor with no format change,
     because the log already files peers under their own kind.
   - **`channel` is keyword-only with no default** (operator, 2026-10-18: "Yes"). mypy then
     points at every `Concern(...)` construction, so `store._restored_concern` can't silently drop
     the channel (run, M3 A: four call-arg errors). Three call sites pass `channel=None` on
     purpose. This departs on purpose from "defaults that claim nothing" for this one field,
     because forgetting it is a silent misfiling.
   - **`send_to_peer` does not accept `incident`** (operator, 2026-10-18: "No"). An incident
     reports something going wrong inside a team, to its own lead. This also leaves
     `bus._withheld`'s "reads it with read_inbox" unreachable for peers (S5 N2).

   **The earlier text of Decided 8, as it stood under measurement, is kept below.**

   ~~8. **A message between leads is its own record kind, `PeerMessage`, built on the `Concern` pattern,
   with its own tools**~~ (operator, 2026-10-17: "The peer messaging earns its own typing even though
   it feels familiar. Letting go of regularity if it's forced. Just trying to limit Don't Repeat
   Yourself").
   - **Why it's a separate type.** The two share an envelope:
     - a sender stamped by the gate;
     - the operator's edit in flight;
     - `delivered_at` as the receipt;
     - filing in the sender's log (Decided 5, 6).

     They differ in meaning, in every behaviour that matters:
     - **authority:** a peer message crosses a boundary between equals; it is quoted, never an
       instruction, and work enters only by a declaration with provenance;
     - **charter:** a peer message is checked against the link's charter;
     - **obligation:** `needs_answer` creates one;
     - **threading:** `re` links replies;
     - **addressing:** a channel the operator made, not a role;
     - **waking:** a new rule, against `_concern_wake`'s worker-to-own-lead-only rule;
     - **`task_id`:** it would be ambiguous across boards.

     As a `Concern` with optional fields, `channel is None` would silently switch all of those, and
     no type check would catch a branch that was missed. As a variant, every consumer `match`es
     over the two message types, and `assert_never` turns a missed case into a mypy error (STYLE §1,
     "Sum types, not base classes").
   - **The record kind.** An older build reads an unknown *kind* as `Unrecognised` and marks the
     board short, but ignores an unknown *field*. So a new kind is noticed where a widened
     `Concern` would be silently misread as an ordinary concern.
   - **The tools are `send_to_peer` and `read_peer_messages`, as the proposal had them.** They say
     what `post_concern` can't carry: a message across a boundary between equals (STYLE §3, "a
     channel that duplicates a path the model already has"). They also keep channel names out of
     `post_concern`'s `to`, where a channel named `builder` would be ambiguous.
   - **On DRY.** The envelope logic (gate stamping, the edit-in-flight arm, delivery stamping,
     filing in the sender's log, the restore arm) is shared by extracting helpers that both kinds
     call where the code really is the same. `Concern` is not reshaped around a shared base.
     Where both kinds keep a deliberate parallel (two record kinds, two restore arms), a test pins
     them to each other, per STYLE §3's "deliberate duplication is fine; unpinned duplication is
     not".
   - **What carries over unchanged from the evidence.** `r5_probe_a.py` proved per-sender filing
     and restore for a cross-session record. The new kind needs its own restore arm, modelled on
     the concern one.

   **Under measurement (2026-10-18).** The case for keeping `Concern` (K: optional fields plus a
   derived peer projection) was argued as devil's advocate and shifted the lead's view to roughly
   even. The operator asked for the question to be measured, "a crossroads of messaging
   implementation": every `Concern` consumer is classified by how a peer message would differ, in
   [`2026-10-18-peer-message-shape/`](2026-10-18-peer-message-shape/), with a skeptic phase. This
   entry stands until that work reports.

   **Superseded the same day.** The earlier ruling is kept below as it was written. Its reuse
   argument still holds for the envelope, and its "still open" question about tools is settled
   above.

   ~~**A message between leads is a `Concern`**~~ (operator, 2026-10-17: "My real lean is not moving
   away from the concern mechanism that's already there"). This is not a new message record.
   - `model.Concern` already has most of what proposal item 2 asks the store to provide:
     - a sender stamped by the gate;
     - `delivered_at` as the read receipt;
     - an operator edit in flight, marked `edited`;
     - an optional `task_id`;
     - `basis`.

     It already files and restores across sessions in the sender's log (`r5_probe_a.py`, run).
     `post_concern` is already in `_REVIEW`, so Decided 1 holds with no new gate.
   - What is new:
     - optional `channel`, `needs_answer` and `re` fields, which grow the format and ride the
       existing kind;
     - routing to a lead in another session (`driver.resolve_role` resolves only within a
       session);
     - a lead-to-lead branch in `store._concern_wake`, under the loop rule still to be decided.
   - The alternatives this rules out:
     - R1's "a new record, not a Concern", since revised;
     - messages in a channel log (Decided 6's rejected option c);
     - SQLite or a shared board as the home of messages;
     - a multi-recipient record.
   - Still open: whether peers are addressed through `post_concern`/`read_inbox`, with `to` naming
     a channel, or through the proposal's separate `send_to_peer`/`read_peer_messages`.
9. **The link record lives in a channel log (H2)** (operator, 2026-10-18: "H2, a new channel log.
   If the shape of the code for that could be postured for a potential SQLite switch with an
   appropriate interface then that seems the more sound").
   - **Where it lives.** A new root, `config_dir()/channels/<name>/`. Not under `boards/`: that is
     `boardsync.migrate`'s legacy root, and it would carry a channel off as if it were a session (run,
     Q1). The log is read once at startup, in the single-threaded slot where board logs are
     migrated, before any session can start. That costs about 2–10 ms for 1–10 logs with a warm
     cache (run, 8 samples; cold cache not measured). Then the writer takes it over.
   - **Why not H4.** In ordinary use, a copy in each member's log makes a member's log permanently
     unappendable. An unlink logged by a member that was closed at link time is a stray (run, Q5 A).
     A link landing on a session mid-resume goes unrecorded, or is silently missing
     (run, Q5 C). Full account: [`q5-link-home.md`](2026-10-17-sqlite-inspection/q5-link-home.md).
   - **Rules that come with it:**
     - linking requires both members running and recording, and unlinking is allowed whenever the
       link is visible;
     - unlinking closes a channel and doesn't delete it;
     - a channel name can't be renamed, because the name is its directory;
     - peer messages are never pushed when a session comes up, only read
       (run, Q5 B: a session's announce can land before its restore).
   - **The SQLite seam.** The channel log sits behind a narrow interface shaped by what the
     SQLite research found any backend must keep. That is an **append-only, ordered sequence of
     typed facts**, keyed by channel, with three operations:
     - append a fact, refusing one that loses a race;
     - read every fact in order;
     - report the log as short or stopped, with the reason.

     It is not a CRUD or query interface. A current-state table loses the properties boardlog
     keeps: contested marking, facts not results, and "short" for an unknown kind (Q1, Q4). Behind
     the seam, a SQLite backend would be one `facts(kind, body)` table whose unknown kinds the
     reader counts (Q2, S2). The file backend ships first. One contract test runs against the
     seam, so a second backend has to pass the same assertions (STYLE §3, pinned deliberate
     duplication).
     - **The cost.** It is an interface with one implementation, and it is kept to exactly the
       operations the file backend needs anyway.
     - **What SQLite would still bring with it if adopted later** (Q2–Q4, run and fetched):
       `isolation_level=None` plus an explicit `BEGIN IMMEDIATE`, and nothing else, on the 3.11
       floor; the WAL-reset bug range for the linked library version; and no hand inspection with
       `ls`/`cat`.
   **Revised 2026-10-18 (operator: "(a) Let's go with that direction"), after RV4's review of the
   first build:**
   - **One log per link, not per name:** `config_dir()/channels/<name>/<link-id>/`. A name is
     unique only among live links.
   - **A link whose log is short or stopped restores as not live.** If the log can't be read
     whole, the link isn't trusted as open: fail-safe, as "unlink wins".
   - **Why:**
     - With one log per name, a damaged record made a later close unreadable, so the link came
       back open after a restart (RV4 finding 1, read).
     - Two windows closing the same link, or one failed append, left a name short for good under
       the "short accepts only a close" rule, and nothing deletes a log (RV4 finding 2).
   - **With one log per link,** damage affects only that one link, and re-linking a name gets a
     fresh log.
   - **The cost:** one failed write closes a live link at the next restart, and the operator
     re-links it.
10. **The wake rule fails toward delivery** (operator, 2026-10-18: "Yes", replacing the narrower
    loop rule the same day). The operator's primary concern: "messages, instructions, decisions,
    etc. that get stalled on delivery just because an agent just forgets to check, which was a
    problem with the POC session." So no message's delivery depends on an agent remembering
    anything. A forgotten step costs an extra turn, never a stalled message.
    1. **Every delivered peer message wakes its recipient by default.** A *wake* is pptmstr
       starting a turn in that lead's session with a short line, such as "N unread on #channel;
       call read_peer_messages". The text itself is fetched by the read, never pushed in the wake.
    2. **Wakes are coalesced per channel.** While a recipient has an unread wake pending for a
       channel, a new message updates that wake's line and adds no second wake. A burst costs one
       turn.
    3. **`quiet=True` is the sender's opt-out**, for acknowledgements and status. A quiet message
       doesn't wake; it's picked up on the next wake or read. Forgetting to mark one quiet costs an
       extra turn, the safe direction. `quiet` and `needs_answer` can't both be set.
    4. **Events from the operator always wake**: an answered decision, an approved parked send.
       They can't start an agent-to-agent loop.
    5. **Backstop:** after K wakes in a row on one channel with no board or operator activity, the
       next wake is held and the channel surfaces in NEEDS YOU. K is set in the build.
    6. **At resume,** a member with non-quiet unread messages is woken only after its restore has
       settled (Q5 B), with a wake line and no message text.
    7. **Decided 11's timer** still surfaces a question that was read and never answered.

    The flows and pseudocode the operator reviewed are in the lead's message of 2026-10-18, and
    are to be carried into the build plan.

    **Refined in round three** (lead, 2026-10-20, from RV-W1 findings 4 and 5, within this
    entry's intent):
    - ~~**The backstop's count resets only on progress that came from that link:** the operator
      acting on that link, or a task whose provenance is that link reaching completed.~~
      **Narrowed again (lead, 2026-10-20, RV-W12 finding 2): only the operator's own acts on
      the link reset it** (its parked sends and channel decisions, answering or dismissing a
      decision raised on it, the release button). A completed task with that link's
      provenance doesn't count, because agents alone can declare, claim and complete a
      throwaway task each turn. Unrelated board activity would also mask a loop for as long as
      either team stays busy. The cost is that a productive exchange surfaces every K wakes
      until the operator acts. K (6) is to be tuned from use.
    - **Wakes the system orders itself** (at resume, on restore) don't count toward the
      backstop.
    - **A recipient's pending wake clears when the wake is sent, not only when it reads.** So a
      lead that's woken and ends its turn without reading is woken again by the next message.
      A forgotten step costs an extra turn, never a stalled message.

    **Superseded the same day.** The earlier rule was: wake only on a message that opens an
    obligation (`needs_answer`) or closes one, and let everything else wait for the recipient's
    next look. It stalled in exactly the operator's case: a sender forgetting the flag, or an idle
    recipient making no tool call to see a notice on. The case for that rule was turns spent on
    acknowledgements (R3: 7 of the pptmstr lead's 23 messages were acks), not an observed loop.
    Coalescing and `quiet` cover that cost.
11. **The "unanswered too long" row** (operator, 2026-10-18: "keep it simple, don't try to buffer or
    infer how 'busy' the reader is. If we start seeing surfacing then that itself is a signal for
    coordination concerns").
    - A fixed age from delivery (Decided 4, 6), with no adjustment for a recipient that is mid-turn.
    - Surfacing goes to the **operator only**. The sender is not told: a notice to the sender would
      be one more wake, and one more way to loop.
    - The mechanism is the team's choice; the simplest that works is a one-shot timer per delivered
      needs-answer, re-armed on restore.
    - How often it surfaces is itself the signal: frequent surfacing points to a coordination
      problem between the two teams, not to a timer that needs tuning.
12. **A channel decision is readable by the channel's members, and the operator's answer wakes
    every member** (operator, 2026-10-18: scope from "I was framing it for forward thinking (N>2
    potential) that's also pairing compatible today … wouldn't that satisfy the need to avoid
    relay or broadcast semantics?"; waking every member, "Yes"):
   - **A decision raised on a channel can be read by that channel's members.** Membership works
     like a subscription that only the operator grants: a lead can't subscribe itself, because
     discovery is the link (proposal item 1). Today a channel has two members (Decided 7), so this
     is the pair. If N>2 is allowed later, the same rule covers N members with no change, because
     the link record already holds a set (Decided 5).
   - **No relay inside a channel:** every member reads the decision directly. **No leak across
     channels:** a lead on two channels doesn't carry a decision from one into the other.
   - **The cost, if N>2 comes:** a channel with N members is a broadcast inside that topic, and
     every message reaches N−1 leads. That is the topology research's O1, and its known problems
     are double action, "answered by whom", and bystanders being woken. Its remedies are sends
     addressed to one member and "answered" tracked per reader. The three-team run comes before
     the relaxation (Decided 7).

   - **The answer wakes every member,** not only the asker. It is an operator event, so it can't
     loop (Decided 10, item 4), and the member who didn't ask is exactly the one a relayed "the
     founder said yes" used to reach late (failure 2).
13. **Decisions are filed in the asker's log** (operator, 2026-10-18: "Yes"; Q1's H6, checked by S2).
    - Each decision has an author session (`Decision.asker`), the same shape as a message under
      Decided 5. So it needs no new root, and only the link record lives in a channel log
      (Decided 9).
    - This makes decisions survive a restart, which `model.Decision` says they don't today. Every
      option needed that, and `boardlog.py` already reserves the kind.
    - **Consequence, as with messages under Decided 6:** while the asker's session isn't resumed,
      its decisions aren't in the store, so the other member can't read them.

## Questions the research raised, and where each was settled

None is open. This section keeps the history.
- Addressability: Decided 7.
- A held message whose sender isn't resumed: Decided 6.
- Where the link record lives: Decided 9.
- A decision on a lead with several channels: Decided 12.
- The idle-time trigger for the age rule: Decided 11.
- How a lead learns of a message that doesn't wake it: dissolved by Decided 10. Only quiet
  messages don't wake, and they arrive with the next wake or read.

5. **Addressability.** The operator, 2026-10-17: "we should address addressability now". So the
   pairs-only recommendation is out. What it covers instead is not settled yet.

   The lead then proposed pairs only, with N>2 reached through coordinator leads (a holon, a
   team of teams). The operator, 2026-10-17: "I have no strong leaning right now and I don't want
   to select a direction just off of vibes." So the topology question is under research, with a
   skeptic phase, in [`2026-10-17-channel-topology/`](2026-10-17-channel-topology/). No option is
   preferred until that work reports.

   **The research reported on 2026-10-17. It does not choose a topology.** The full account is
   [`r5-skeptic-response.md`](2026-10-17-channel-topology/r5-skeptic-response.md) §2–3. In summary:
   - **The record layer is the same under every option, and it is no-regret:**
     - a link record that holds a *set* of members, where a pair is a set of two;
     - decisions that every member can read, which first needs decisions to be logged;
     - messages addressed to one reader and filed in the sender's log.

     The board-log format only grows, and a build older than a field ignores it. So a record created as a pair
     and widened later would be misread as a pair. Its shape has to be decided before anything ships, whichever
     topology wins.
   - **Enforced direction (O3) is weakened.**
     - The evidence counts against trees with more than one level (MacNet, MARBLE).
     - The one result that seemed to favour a fixed direction (MAST's ChatDev) turned out to be a supervised
       review loop.
     - The PDFs (R6) suggest MacNet's tree is probably about four agents in two levels, set against a one-level
       star. So "against deep trees" overstates it.
   - **Group channels (O1) and a one-level coordinator star (O2) are close.** The evidence that a hub contains
     errors comes from a hub that checks and combines work. O2's coordinator only forwards. So that evidence
     supports neither option.
   - **The best-supported shape is the skeptic's "split by kind".** The group carries decisions and status, and a
     needs-answer must name one member. Nobody has walked it through every scenario.
   - **What would settle it** is a three-team run with three arms: unaddressed sends, `to`-addressed sends, and
     needs-answer forbidden on the group. It would count double actions and how often a third team needs the same
     decision. It needs a prototype channel, or the file protocol extended to three folders.

   The research found these new questions; none is decided:
   - **Decided 4 as built.** A held message lives only in its sender's log, so resuming the recipient alone does
     not bring it back (`r5_probe_a.py` A5, run). Either resuming a member also restores its linked peers' logs, or
     a held message is also written on the recipient's side. The second runs into the restore's refusal of an id
     another session holds.
   - **Where the link record lives.** It belongs to two or more sessions, so it fits no one session's log.
     The operator, 2026-10-17: "I think sqlite might be worth inspecting. If it's rock solid and helps
     with interesting datastores then why not?" This is under research, with a skeptic phase, in
     [`2026-10-17-sqlite-inspection/`](2026-10-17-sqlite-inspection/). The scope is a home for facts
     no single session owns (the link record, channel decisions), beside boardlog. Replacing
     boardlog is out of scope.
   - **Decided 3 on a lead with several channels.** Is a decision readable on every channel the asker is on, or
     only on the one it was raised on?
   - **Decided 4's age rule needs an intent applied while every session is idle.** Today `needs_you` changes only
     when an intent is applied.

## Not measured

All of this comes from reading and from the 2026-10-17 run's two message folders. No code was
written for it. The symbols cited were checked by grep on 2026-10-17 and nothing more. Whether a
model uses a peer channel well (answers needs-answer, threads with `re`, raises a decision instead
of relaying one) is unknown until one is built and run.
