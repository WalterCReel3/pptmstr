# Building the inter-lead channel

**Dated:** 2026-10-18 · **Status:** round one built, reviewed and gated. `make check` is green
(13296 passed). `typecheck-all` is at 2006, against 2015 at the branch point. Rounds two to four
not started. **Builds:**
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

- **The channel log (Decided 9).** It lives at `config_dir()/channels/<name>/`, behind the
  append-only seam: append a fact, refusing one that loses a race; read every fact in order;
  report short or stopped. One contract test runs against the seam. It is read once at startup
  in the migrate slot.
- **Link and unlink as operator acts**, with the closed-card rule. Linking refuses a member set
  that isn't exactly two (Decided 7). Unlinking closes and never deletes.
- **`send_to_peer` / `read_peer_messages`**, both in `_REVIEW`.
  - Routing reaches the peer's lead across sessions through the live link; today `resolve_role`
    resolves only within a session.
  - `quiet` and `needs_answer` can't both be set, and `incident` is refused.
  - Reading writes `Delivered`.
- **Channel decision readability (Decided 12):** readable by the channel's members.

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

**Into round four:**
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
