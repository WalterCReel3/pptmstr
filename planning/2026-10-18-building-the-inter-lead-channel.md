# Building the inter-lead channel

**Dated:** 2026-10-18 · **Status:**
- Round one is built, reviewed and gated (13296 passed; `typecheck-all` at 2006).
- Round two is built, reviewed and gated on 2026-10-19: `make check` green with 13586 passed, and
  `typecheck-all` at 1918. Round two is the channel log, linking, the peer tools, channel
  decisions and the minimal Link leads UI.
- Rounds three and four haven't started.

**Merged origin/main (graph tooling, 940da8e) on 2026-10-19.**
- 14 conflicted files, resolved keeping both sides.
- `make check` green: 13921 passed, 23 `[imgui-error]` lines.
- `typecheck-all` is 1952, the new baseline. It was 1918 on this branch and 2045 on main.
- `model.withdrawal_notice`, main's, is a fourth team-concern constructor, now passing
  `channel=None`.
- Untested: a dry-run `declare_task` carrying provenance, or edited by the operator. Its reply
  omits the charter-unkept sentence, as main's dry run omits "basis not kept", and the combined
  "DRY RUN" + provenance queue row is unpinned. That's for round three.

`make shot-xvfb` can't keep an image outside the tree: its namespace tmpfs hides `$TMPDIR`. So
a gate's shot step proves only that a frame rendered under Xvfb. **Builds:**
[`2026-10-17-two-leads-in-one-window-share-a-channel.md`](2026-10-17-two-leads-in-one-window-share-a-channel.md),
Decided 1–13. This record covers order and acceptance only. Reasons stay in the plan and its three
research directories, and are not restated here.

## Verification baseline

The worktree now has a composite `.venv` (`scripts/worktree.py`'s `build_composite`, borrowing the
main checkout's site-packages; offline). `make check` ran green on 2026-10-18: ruff, black,
mypy (85 files), `lock-gate` (no release lock yet), and pytest with 13049 passed. Every round's
gate is `make check` run from this worktree.

Interpreter rules, learned the hard way during the research:
- Running a planning script directly (`python script.py`) imports the main checkout's `pptmstr`
  unless `PYTHONPATH` is set to the worktree.
- mypy ignores `PYTHONPATH`.
- pytest breaks one test (`test_typeset_deps`) when `PYTHONPATH` is set.

`make` from the worktree avoids all three.

## Rounds

Each round ends with a reviewer pass and a gate. A later round is boarded once the round before it
is green.

### Round one: the record layer

The tasks share `model.py`, `boardlog.py`, `boardfacts.py` and `store.py`, so the board runs them
in sequence. The order puts the format decisions first.

1. **R1-hplus: peer messages as `Concern` with a `peer-posted` kind** (Decided 5, 8).
   - `Concern` gains `channel` (keyword-only, no default), `needs_answer`, `re` and `quiet`.
   - Peer-ness is derived as `channel is not None`.
   - `inbox_of` excludes channel mail; a new `peer_inbox_of` takes it.
   - `boardfacts._filed` writes a channel concern as `peer-posted`. `boardlog` gets the kind and
     its restore arm, and `store._restored_concern` carries the field.
   - The three call sites that build a team concern pass `channel=None`.
   - **Acceptance, in this change:**
     - the kind pin (m2-judgement.md, "H+: the pin for `_filed`");
     - the end-to-end round trip through `BoardRestored`, with `test_boardfacts`'s `_concern`
       generator setting `channel`;
     - the inbox split pin (disjoint, and covering every waiting concern).
   - No tool sends a peer message yet. Routing is round two.
2. **R1-decisions: decisions in the asker's log** (Decided 13).
   - Record kinds for a decision raised, answered, withdrawn and dismissed. `boardlog.py` already
     expects "a decision raised and answered".
   - `boardfacts.outcomes` arms, filed in the asker's session, and a restore arm.
   - `Decision.channel: str | None`, stored but not yet read: readability is round two.
   - The `model.Decision` docstring's "does not survive a restart" is updated.
   - **Acceptance:** a decision raised, answered and restored into a fresh store reads back whole;
     an older build reads the new kinds as `Unrecognised`.
3. **R1-provenance: provenance on declarations** (Decided 2).
   - `Task` gains an optional provenance: channel, the message it answers (`re`), and
     inside-charter as yes / no / unstated.
   - It rides the `Declared` record.
   - `declare_task` accepts it; the parked declaration row and DETAIL show it.
   - **Acceptance:** it round-trips through the log, and `test_boardlog`'s field accounting
     passes.

### Round two: links and routing

- **The channel log (Decided 9).** It lives at `config_dir()/channels/<name>/<link-id>/`, one log
  per link (Decided 9's revision), behind the
  append-only seam: append a fact, refusing one that loses a race; read every fact in order;
  report short or stopped. One contract test runs against the seam. It is read once at startup
  in the migrate slot.
- **Link and unlink as operator acts**, with the closed-card rule. Linking refuses a member set
  that isn't exactly two (Decided 7). Unlinking closes and never deletes.
- **`send_to_peer`** in `_REVIEW`, and **`read_peer_messages`** in `_BUS_AUTO`, like
  `read_inbox` (corrected; see Decided 8).
  - Routing reaches the peer's lead across sessions through the live link; today `resolve_role`
    resolves only within a session.
  - `quiet` and `needs_answer` can't both be set, and `incident` is refused.
  - Reading writes `Delivered`.
- **Channel decision readability (Decided 12):** readable by the channel's members.

- **Pulled forward from round four** (operator, 2026-10-18: "Add the minimal 'Link leads'"):
  - Session ▸ Link leads… (a small form: the peer, a name, a charter) and Close channel on the
    session menu;
  - the card's line per live channel (`#name ↔ peer`, plus the unread peer count).

  These are B7-link-ui and RV7-link-ui. They run beside B5 once B4-perlink is reviewed, because
  their files don't overlap.

### Round three: wakes and surfacing

- **The delivery-first wake rule (Decided 10):**
  - wake by default;
  - coalesced per channel;
  - `quiet` doesn't wake;
  - operator events always wake, and an answered channel decision wakes every member;
  - the K-wake backstop;
  - wakes at resume only after the restore settles, and never carry message text.
- **The unanswered-question age (Decided 11):** a one-shot timer per delivered needs-answer,
  re-armed on restore, surfacing to the operator only.
- **NEEDS YOU kinds** for an unanswered peer question and a held wake chain.

### Round four: surfaces

- Session ▸ Link leads, with a channel name and charter.
- The card's channel line and unread count.
- The DIGEST thread.
- The charter shown beside a parked declaration that has provenance.

## Carried forward from round one's reviews

**Into round two:**
- `Snapshot.peer_inbox_of(node, channel=None)`: here `None` means "every channel", while on
  `Concern.channel` it means team mail. Make the two meanings distinct before a caller passes one
  through as the other (RV1 finding 3).
- `board_concerns`, `_open_concerns_by_task` and `fleet_view` don't yet separate peer messages
  from team concerns (RV1 finding 4).
- S4 finding 7's guard: a `peer-posted` record whose recipient has no recorded id is refused on
  restore, never placed with the sender's own lead (RV1 finding 5).
- An empty channel name is refused at link time (RV1 finding 6).

- **Peer message ids must have the shape `model.is_concern_id` accepts:** `c-` plus 12 lowercase
  hex, as `bus.post_concern` mints them. Provenance's `re` is checked strictly against that shape,
  so `send_to_peer` either mints ids the same way or changes `is_concern_id` in the same change
  (B3-fix).
- `read_board`/`read_task` print a task's provenance, so a lead or builder can see that a task was
  requested by a peer, and which message it answers (B3).

**Decided during round one (lead, 2026-10-18), recorded here:**
- **An operator's edit drops the declarer's charter mark.** When the gate stamps EDITED_KEY on a
  `declare_task`, the task keeps `channel` and `re`, and `inside_charter` becomes unstated. The
  stamp doesn't say which arguments changed, and the mark is self-reported, like `basis`. On a
  reviewed rung the operator has just judged the request themselves (B3; pinned by a test).
- **Provenance rides `Declared` as fields, not a new kind.** An older build restores the task
  without its provenance and doesn't mark the board short. This differs from `peer-posted` on
  purpose. A peer message misread as team mail changes what an agent does with it, while a task
  missing its provenance is still the same work, and an older build has no channel to judge it
  against anyway (B3).

**Decided during round two (lead, 2026-10-18):**
- **The channel seam has four operations, not three:** `append`, `read`, `state`, and `names()`.
  Startup has to enumerate whichever backend it is given, and `names()` is a listing of keys,
  not a query over facts (B4).
- **"Recording" is the shell's statement, carried on `ChannelLinked`.** The store can't see
  `board_logs` (Q5 F8), so `app.link_leads`, the only builder of the intent, states which members
  were recording. "Running" is the store's own check (B4).
- **Superseded by the per-link layout (Decided 9's revision, B4-perlink):** the two rules
  B4/B4-close-only built, "a short or stopped log accepts only a close" and the two meanings of
  "stopped". With one log per link, a short, stopped or damaged link restores as **not live**,
  and the name stays usable, so neither rule is needed. Both are kept here as history.
- **Accepted in B4-perlink (lead, 2026-10-18):**
  - Several live links under one name (two windows linking at once) are **all** withheld, with
    none preferred.
  - A name directory that can't be listed makes the whole listing unlisted, so every link is
    withheld for that run. That's coarse, but it fails safe.
  - A repeated close of a closed link is an idempotent no-op, not a contested fact.
  - The seam keeps `state` beside `read`, and the contract pins `state == judge(read)`.
- **A known hole, accepted (lead, 2026-10-18), in the unsafe direction.**
  - The failure: a link's close fails to write, and so does the stop marker after it, as on a
    full disk. The log then reads whole with only the link in it, and the link restores **live**
    at the next start.
  - ~~What the operator is told: `close_channel` reports "closed for this run only". It needs two
    consecutive write failures, and it's reported when it happens.~~ **Corrected (RV4b findings 2
    and 3, read):**
    - A close is lost in ONE event: a quit while a slow append is still writing (the writer gives
      up at its timeout and writes no marker), a kill just after the close, or a power cut before
      the directory change reaches disk.
    - The last two are never reported.
    - When the write itself fails, `close_channel` has already returned None, and only the
      writer's drain logs it. B7 makes that outcome reach the operator.
  - Board logs have the same durability model, and this is accepted on the same terms. The log is
    intact, so it falls outside "damaged or unreadable".
  - ~~**The mechanism that heals it:** the next link of the name closes every unclosed earlier
    link under it, so a lost close is cleared by the operator's next link.~~ **Corrected (RV4c
    F4, read):** a link whose close was lost restores LIVE. So `link_leads` refuses the name
    ("already live"), and what heals it is the operator closing it again, not a relink. The name
    isn't stuck: it costs a second close.
- **Accepted (lead, 2026-10-18; RV4c F2):** a link directory that is readable but not writable,
  for example one left by a run under sudo, can take neither the sweep's close nor a stop marker,
  so its name stays withheld at every start whatever is relinked. That's an environment fault
  outside "read errors, races, build switches". The fix is the operator restoring the
  directory's ownership. The startup warning names the directory, so it can be found.
- **Linking a name first closes every unclosed earlier link under it** (lead, 2026-10-18, from
  RV4b's suggestion). Without that, a withheld link, which never enters the store and so can't
  be closed, kept counting as live at every start. One transient read error plus a relink then
  made a name unusable for good (RV4b finding 1, read). A close is never unsafe, and it uses the
  existing kind, so there's no format change.

- **The relink sweep closes every unclosed earlier link under the name, including one live in
  ANOTHER window** (B4-relink-closes, accepted by the lead 2026-10-18).
  - The case arises only when window A linked the name after window B started; otherwise B's
    store holds A's link and refuses B's link as "name already live".
  - It resolves the race toward the later link, which is "unlink wins".
  - A keeps showing the link until it restarts. ~~Messages A sends over it are filed but not
    handed over once the link reads closed.~~ **Corrected (RV5, read):** A's store never re-reads
    channel logs, so in A's process the link never reads closed. A's sessions keep sending and
    handing over messages for the rest of A's run, and the hold starts only at A's next start.
    "Unlink wins" holds on disk, not in A's running store.
  - The narrower alternative, sweeping only links this process saw withheld, fails to heal a
    link that was withheld at one start and readable at the next.

- **A peer message names the link it was sent over** (lead, 2026-10-18, from builder-41's note).
  - The field is `Concern.link`: the link id, keyword-only, set exactly when `channel` is.
  - With one log per link, a name is reused across links. So filtering and liveness by name
    would let a message sent over a closed link be counted under, and handed over through, a new
    link of the same name.
  - Liveness, the peer inbox and unread counts all key on the link. The name is for display.
  - `peer-posted` exists only in this unmerged branch, so the field costs nothing against older
    builds (B5).

- **The relink sweep's rule, as built (B4-sweep-narrow, accepted by the lead 2026-10-18):**
  - **Close a link that could come back live:** a whole, unclosed log; one short only for an
    unknown kind or a member count other than two (a newer build might read either as live); and
    one that can't be read right now (EACCES, EIO), since a later read may succeed.
  - **Skip** a damaged log (a missing ordinal, or an undecodable record) and one whose stop marker
    landed.
  - **It decides from the read, not from "this process stopped writing it".** On a full disk the
    marker is lost too, and that log reads whole.
  - **Its cost** is bounded by a per-writer memo of links already settled. The first relink in a
    process still reads every earlier log under the name.
  - `channels.py` reaches into `boardlog._name` to tell damage apart from a read failure. A field
    on `boardlog.Damage` would be cleaner, later.

- **A peer `ConcernPosted` that isn't lead-to-lead across two sessions is a no-op in the
  reducer** (B5, accepted). `PeerSent` is the only way a peer message enters the store, and
  without the no-op the store could file a shape its own restore refuses as incoherent.

- **Channel decisions are lead-only, both ways** (B6, accepted). Only a lead raises a decision on
  a channel, and only the lead of a member session reads one. Only leads speak across a link
  (proposal item 7). Otherwise a peer's worker would see more of the other team than the asker's
  own workers do.

- **A decision raised with a channel is reviewed like `send_to_peer`** (lead, 2026-10-18,
  Decided 1; RV6 finding 1).
  - Without a channel it stays auto-approved.
  - The NEEDS YOU row isn't that review, because the peer lead can read the decision as soon as
    it's filed, before the operator sees the row.
  - Before B6, a decision reached only the asker's own lead, which is what made auto-approval
    safe.

- **A peer `send` whose `re` names a message the store doesn't hold is refused** (lead,
  2026-10-19; RV5b finding 4).
  - Without that, after a restart where the original sender's session isn't resumed and the name
    was relinked, a reply written for S2 reached S3.
  - It's the fail-safe direction. The cost is that a lead occasionally resends without `re`.
- **A send or channel decision is bound to the link that was live when the agent made the
  call** (B5-fix).
  - The gate stamps the call time, and the store compares it with when the link went live, on
    one monotonic clock.
  - A link made after the call refuses it.
  - The comparison is `>=`, so a tie counts as after. The frame clock is read straight before
    `apply_intents`.
  - A sub-millisecond residual is accepted: a gate stamp taken during the batch's own drain and
    apply counts as after. Closing it would take a post-apply stamp or a new intent (B5-fix2).
  - The parked row predicts only the "linked after" refusal. The other two refusals (an `re` not
    held, an `re` from another link) depend on resume state the row isn't rewritten for. The
    refusal text gives the true cause (B5-fix2).

- **A standing tax on test typing** (T-r2-types, run).
  - Every new `Intent` or `Effect` variant adds a `typecheck-all` error at each remaining
    `union.attr` site in tests that already fails.
  - Round two fixed those sites with casts, which also cleared 88 old errors on the same lines:
    `typecheck-all` went from 2006 to 1918.
  - Expect the same in round three, and fix such sites as they come up.

**Into round three:**
- **A peer lead isn't told when a channel decision is raised or answered.** It finds out only by
  calling `read_decisions`. Under Decided 10 (delivery-first), raising a channel decision should
  wake the peer lead, coalesced like a message, and the operator's answer wakes every member
  (Decided 12) (B6).

**Into round four:**
- The operator's NEEDS YOU row and DETAIL don't show that a decision is on a channel, so the
  operator can't see that the peer reads the answer (B6).

**Into round three (continued):**
- ~~The `app._report_notice` docstring says the new link "stays linked".~~ Done in B7-notices.
- Qualify "kept in the sender's log" as "if that session was recording its board when it was
  sent" at `effects.py` (PeerInboxDelivered.held), `intents.py` ~390, `board.py` ~806 and
  `boardfacts.py` ~457. The tool text already says it (B5-fix2). These are comment and docstring
  changes only.
- **Messages held on a closed link need an operator surface.**
  - A message waiting on a link that has since closed is never handed over. `read_peer_messages`
    tells the lead to ask the operator, but nothing shows the operator those messages.
  - NEEDS YOU (round three) or the DIGEST thread (round four) shows them, so "try not to drop
    messages" means more than "kept on disk" (B5).
- **`test_boardfacts`' generator is fragile.** Its case "reclaim from a same-window earlier run"
  turns up in about 1 of 80 seeds and vanishes if the order of the random draws changes. B5
  injected peer intents from a second RNG to keep it. That case needs a deterministic test of
  its own (B5, run).

**Into round four (and a display check):**
- **The sweep's unlisted case is still shaped as a `LinkClosed` reported against the new link.**
  It's tagged `Outcome.swept`, and `app._read_channel_reports` tells it apart. A reader of
  `Outcome` that ignored `swept` would misreport it, so it should become a report type of its own
  in `drain()`'s union. B7-notices kept it small because that change touches every consumer of
  `drain` and `close`.
- Nothing from B7 has been seen by eye:
  - whether `↔` renders in the body face (Inconsolata Medium wasn't checked);
  - the form's wrapping;
  - combo keyboard behaviour;
  - Escape not closing the form while a text box is active.
- When the sweep can't list a name's earlier links, the report is attached to the NEW link's
  outcome. `app._read_channel_reports` then logs it as "#name (new-id): a LinkClosed did not reach
  the link's log…", which reads as if the new link's own close failed. Give it an outcome kind
  that app logs in its own words (B4-relink-closes).
- `app.link_leads` returns `ChannelLinked | channels.Refused`, and `app.close_channel` returns
  `str | None`. The UI handles both (B4-close-only).
- `Channel.created_at`/`closed_at` are wall-clock times, so they survive the log. Don't compare
  them with the monotonic clock behind `needs_you` and `since` (B4).
- `app._newest` and the board pane's restore line count tasks and concerns only, not decisions
  (B2).
- On AUTONOMOUS, the board pane shows a landed task's provenance. Nothing parks there, so it is
  the only operator surface for it. DETAIL never draws a Task (`ui/detail` module docstring). It
  uses `model.Provenance.said()` for the wording (B3).
- `_EDITED_NOTICE`/`_WITHHELD` (`detail.edit_differs`, `inbox.edit_withheld`) decide "edited" by
  comparing strings, while the driver stamps `_edited` by comparing dicts. A draft that only
  reformats the JSON is sent, but the board treats it as unedited. The provenance warning
  follows the dict rule, so the two lines can imply different things. This predates round one
  (B3-fix2).
- A `bus.declare_task` test with an undrawable channel. The refusal is tested in
  `stated_provenance`, but not end to end, because `test_bus.py` was outside B3-fix2's touches.
- The field-column cap (about 112 px plus spacing) still pushes values off a pane narrower than
  that. RV3b's R6, measuring the narrowest layout, needs a display.
- The parked row has a second cut, by pixels, after the 90-character clip: `ellipsis(…, width -
  summary_x - 12)`. The charter mark now sits early in the row, so it survives the clip, but not
  at the narrowest pane the layout allows. That needs a display to measure (B3-fix).

**Rule for every implementation task, after two incidents in round one:** a mutation check or a
diagnostic edit runs on a scratch copy under `$TMPDIR`, never in the tree. Another builder may
hold the same files.

## The flows the operator reviewed (2026-10-18), condensed

```python
send_to_peer(sender, channel, subject, body, needs_answer=False, re=None, quiet=False):
    link = channels.live(channel); require sender.session in link.members
    require not (quiet and needs_answer)
    msg = Concern(..., channel=channel, ...)   # stamped by the store; filed as peer-posted
    if not quiet: request_wake(link.other(sender).session, channel)

request_wake(session, channel):
    if not session.running: return             # held; woken on resume, after restore
    if wake_pending(session, channel): update_line(session, channel); return
    if chain_count(channel) >= K: needs_you.add(WakeChainHeld(channel)); return
    wake(session, f"{unread} unread on #{channel}; call read_peer_messages")

read_peer_messages(lead, channel):
    for m in peer_inbox_of(lead, channel): Delivered(m); if m.needs_answer: start_timer(m)
    clear_pending_wake(lead.session, channel); return quoted(msgs)

on_timer(m): if not answered(m): needs_you.add(UnansweredPeerQuestion(m))   # operator only

on_operator_answers(d): wake(d.asker.session)
    if d.channel: for member in members(d.channel) - {d.asker.session}: request_wake(member, d.channel)
```

## Not settled by this record

- K, the backstop's run length; it is round three's to set and justify.
- Whether a cold-cache read of the channel log at startup matters. It hasn't been measured, and
  it needs root.
- The three-team run that comes before relaxing pairs-only. It isn't part of this build.
