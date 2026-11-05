# R1: what each channel topology costs in this code

**Dated:** 2026-10-17 · **Task:** R1-code-cost · **Basis:** read. No code was written or run.
Every claim below comes from reading the tree at `e57b027` (grep, sed, Read). Line numbers are
from that reading. Symbols are named next to them so a citation still holds after an edit.
Tags: **[read]** means read in the file cited. **[relayed]** means taken from a planning
record and not checked against code. **[inferred]** is my reasoning from read facts, not a
fact read. Nothing here is **[run]** or **[fetched]**. No option is recommended.

Options under comparison (from the task): **O1** group channels; **O2** pairs only, N>2
through coordinator leads that forward by reference with a provenance chain; **O3** pairs with
an enforced parent/child direction; **O4** pairs plus fan-out ("ganging"). I added **O5**, and
say so in §2.5. It is noted, not costed.

---

## 0. Six facts about the tree that every option meets

These shape the core more than the topology does. All six are [read].

1. **One fact goes to exactly one session's log, and a restore refuses anything that is not
   that session's.**
   - The rule is stated at `boardfacts.py:33` ("A fact goes to one session's log"), and
     `Fact.session_id` is a single `str` (`boardfacts.py:123`).
   - A concern's facts go to its *sender's* log only (`boardfacts._filed`, `boardfacts.py:435`).
   - On restore, a concern whose sender is in another session is `Unplaced.NOT_THIS_SESSIONS`
     (`store._restored_concern`, `store.py:2992-2993`).
   - An id another session already holds is `OTHER_SESSION_HAS_ID` (`store.py:2729-2737`).
   - Either one makes `Restore.short` true. A short restore is never `adoptable`
     (`model.py:2627`), so that session's log is **never appended to again** by the window that
     restored it.
   - Consequence: a record about a cross-session channel cannot go into both members' session
     logs, and cannot go into the recipient's log alone, without ending the recording of a
     session. This holds for every option.

   **Revised (S1 finding 1; R5, basis run).** The facts above hold; the consequence drawn from
   them was too wide. A message filed in its *sender's* log alone triggers neither refusal, and
   the tree already does exactly that for a concern to another session.
   [`r5_probe_a.py`](r5_probe_a.py) posts a concern from session A's lead to session B's lead
   through `store._apply`, writes the facts through the real `BoardWriter` to disk, and restores
   with `boardsync.find` + `BoardRestored` into a fresh store (output in
   [r5-skeptic-response.md](r5-skeptic-response.md), item A):
   - the `Posted` and the `Delivered` for B's read both go to **A's** log; B's log directory is
     never created;
   - restoring A's log places the concern with recipient B's lead, `DELIVERED`, `delivered_at`
     set; `unplaced == ()`, `short` False, `adoptable` True, with or without B's root in the store;
   - undelivered, it is in `inbox_of(B lead)` after A's restore;
   - **if only B is resumed, the message is not in the store at all** (A's log is never read).

   So what does not fit a session log is the *link record* (it belongs to two or more sessions),
   not the messages. The limit is routing, not the record: no bus tool can address another
   session's lead today (fact 2), so the probe made the address in the store.

2. **Mail never crosses a session today.**
   - `AgentSession.resolve_role` resolves only the caller's own roles plus
     "lead/main/root" → its own root (`driver.py:1456-1468`).
   - `board.board_concerns` says a concern to another session "cannot arise today (roles
     resolve within a session)" (`board.py:796-798`).
   - Every root is called `lead`, so a cross-session address cannot be a role name. A channel
     needs routing held in the store snapshot, not in `AgentSession`.

3. **No wake crosses a session, and that is deliberate.** `store._concern_wake` wakes only on a
   worker's concern to its own lead, "so no agent can wake another into a loop … anything across
   sessions are read with `read_inbox`" (`store.py:2099-2120`, rationale at `:2104`). **Every
   option breaks this on purpose** (proposal item 2: delivery wakes the recipient). §3 says how
   the loop exposure differs by option.

4. **A wake for a session that is not running is dropped.**
   - `app._send_wakes` skips a root that has no record, is recalled, is in `_UNWAKEABLE`, or is
     off the fleet, and "its wakes are let go of" (`app.py:1110`, `:1124-1150`).
   - `SessionPool.wake` returns False when there is no client (`pool.py:320-329`).
   - Decided 4 ("held and handed over when that session resumes") therefore needs a new trigger
     on resume. The existing path cannot be reused unchanged.

5. **`needs_you` is recomputed only when an intent is applied.**
   - `_owing_decisions` and `_owing_commands` run inside `store._apply` (`store.py:1431-1440`,
     `:2428`). STYLE §1 forbids the reducer to read a clock.
   - No intent in `intents.py` is a timer: there are 44 classes and none is a tick (`intents.py`
     class list).
   - So Decided 4's "surfaces in NEEDS YOU after a fixed age" has no existing mechanism. It needs
     either a periodic shell intent that carries `now`, or an age test done at draw time outside
     `needs_you`. The second makes STYLE's single `needs_you` projection (STYLE.md:62-64) have
     two sources again.

6. **Decisions are not in the board log** (`model.Decision` docstring, `model.py:629`;
   `boardfacts.outcomes` returns `()` for every decision intent, `boardfacts.py:164-175`).
   - Decided 3 makes channel decisions readable by every member. Proposal item 5 makes "the
     thread" survive a restart.
   - As built, a channel's decisions would **not** survive a restart even when its messages do.
   - Whichever option is picked, someone has to rule on this. See §4.

---

## 1. The shared core: what every option needs

Each item names the closest thing the tree has now, and what is new.

| Piece | Nearest existing thing | What is new | Size |
|---|---|---|---|
| **Channel record**: name, charter, the two (or N) member session ids, linked_at, linked by the operator only | none (grep for `channel`/`peer`/`link` in `pptmstr/` finds no such concept) [read] | `model.Channel`, `Snapshot.channels`; intents `ChannelLinked`/`ChannelUnlinked` that name no node, as operator intents already do (`store.py:1414-1416` comment) | M |
| **Message record**: id, channel, sender (gate-stamped), subject, body, `needs_answer`, `re`, posted_at, per-reader delivered_at | `model.Concern` (`model.py:1506`): gate-stamped `sender` and a single `recipient`, with state POSTED/DELIVERED/WITHDRAWN and `edited` | A new record, not a Concern. `Concern.recipient` resolves within a session (fact 2), and Concern restore is sender-session-only (fact 1) | M |
| **Store stamping** (order, wall time, sender, receipts) | `ConcernPosted` takes the sender from the gate (`intents.py:297-307`); `InboxRead` stamps `delivered_at` (`store.py:763-781`); `bus._sender` (`bus.py:499`) | One new set of arms per message intent. Ordering is the store's `seq`. Wall time needs `Clock.wall_at` as boardfacts already uses (`boardfacts.Fact.on_the_wall`) | S |
| **"Answered"** | none | Derivable: there is a reply from that reader with `re == id`. STYLE §1 "Derive; do not store" (STYLE.md:55-69) says not to store it | S |
| **Wake on delivery** | `effects.SessionWoken` (single `session_id: str`, `effects.py:330`), `WakeReason` (`effects.py:320`), `bus.wake_line`, a `match` closed by `assert_never` (`bus.py:4081-4109`), `app._hold_wake`/`_send_wakes` (`app.py:1113-1150`) | A new `WakeReason` member and a `wake_line` case. The arm emits one `SessionWoken` per recipient session. The effects tuple already carries several | S |
| **Held delivery** (Decided 4) | none: fact 4 | On resume, after the store holds the channel, emit wakes for undelivered messages to that session. The natural place is beside the restore in `app._launch` (`app.py:2460-2471`), which already orders restore before `pool.submit` | M |
| **Persistence** | `boardlog` one-file-per-record, `boardsync` keyed by **session id** under `sessions/<id>/board/` (`boardsync.py:4-8`, `BoardWriter.submit(session_id, …)` `:557`) | Per fact 1, channel records cannot live in session logs. That means a **channel-keyed log root** (e.g. `channels/<name>/`) and a restore for it that is not triggered by one session's `spec.resume` (`app.py:2460`). It also means a `Restore`-like report keyed by channel: `Snapshot.restores` is keyed by session (`model.py`, `Snapshot.restores`). New record kinds (`boardlog.py:63-67` lists the four steps), all fields limited to `_FIELD_TYPES` (`boardlog.py:137`, asserted at `:478`) | L |
| **Channel name as a path component** | `requests.address_refusal` (`requests.py:460-482`) already checks an address is safe as one component | The channel name becomes the log directory's name, so renaming a channel would start a second history | S |
| **Gate** | `_REVIEW` (`approval.py:170-231`) with `_AUTONOMOUS_AUTO = _REVIEW` (`approval.py:233`); `post_concern` parks by being in `_REVIEW` | `send_to_peer` goes into `_REVIEW`, so Decided 1 (sender's rung) holds with no new mechanism. `read_peer_messages` goes into `_BUS_AUTO` (`approval.py:352`). Tool lists are pinned by tests (`tests/test_bus.py:1106-1136`, `:1391`; `tests/test_templates.py:328-330`) | S |
| **Provenance on a declaration** | `Task` has no requester field (`model.py:2054-2185`); `Declared` record | New `Task` fields (requested on channel X, re message Y, inside-charter tri-state, self-reported on AUTONOMOUS per Decided 2). A new Task field must ride a record or be listed as left out, or `tests/test_boardlog.py` fails (`boardlog.py:79-85`) | S |
| **Channel decisions** (Decided 3) | `Snapshot.decisions_readable_by` (`model.py:2798-2817`): your own, or every decision in your session if you are the root | `Decision.channel: str \| None` and a third branch in `decisions_readable_by` that adds every channel the caller's session is a member of. `DecisionAnswered` wakes only the asker's session (`store._woken`, `store.py:2091-2096`); waking the other members is new | S |
| **NEEDS YOU after an age** (Decided 4) | `ObligationKind` (5 members, `model.py:377-398`) and its consumers: `theme.py:280-311` (two dicts), `ui/rail.py:348-352` badge order, `digest._owed_name` (`digest.py:555-568`), `ui/inbox.py:747`, `:1006`, `bus.py:1913-1915` | A sixth kind plus the timer from fact 5 | M |
| **UI** | card badges per obligation kind (`ui/rail.py:356-366`), `digest.digest(snap, session_id, since)` (`digest.py:268`), DETAIL | Channel line on the card, a thread section in DIGEST, Session ▸ Link leads, the charter shown at the parked declaration | M |

~~**Core rough size: L.** The two items that make it L, not M, are both forced by fact 1: a log
root keyed by something other than a session, and a restore not driven by a single
`spec.resume`. Neither depends on the topology. [inferred]~~

**Revised (S1 finding 1; R5).** Two rows above are wrong for messages. The *Message* row's
reason ("Concern restore is sender-session-only") is the property that makes per-sender filing
work, not an obstacle: a cross-session concern already files and restores in its sender's log
(run, `r5_probe_a.py`). A single-recipient message (a pair message under O2/O3, each copy of an
O4 fan-out) is close to the existing `Concern` machinery. A multi-recipient record (O1 without
`to`) is the new kind. The *Persistence* row's channel-keyed log root is then needed for the
**link record** (and for decisions, fact 6), not for the thread. Rough core size for messages
drops toward M; the link record's home is still unsettled and still L if it needs its own root.
New costs that the revision exposes, and that grow with the number of senders on a thread
[inferred from the run]:
- a thread is the union of its senders' logs, so it is whole only when every sender's session
  is resumed: 2 logs for a pair, N for an O1 group;
- Decided 4's held message lives in the sender's log, so a resumed recipient whose sender was
  not resumed never sees it (probe item A5).

---

## 2. Per option: the delta from the core

"Single-valued" asks this question of each fact: with this option, is it still one value per
message?

### 2.1 O1: group channels (N leads on one channel)

**New records and fields**
- Channel membership becomes a set.
- If members can change after linking, there are `MemberJoined`/`MemberLeft` intents and
  records.
- The message needs a recipient set at post time, so that a late joiner does not owe answers to
  messages posted before it joined. That set is either stored on the message (`tuple[str, ...]`
  fits `_FIELD_TYPES`) or derived from membership records ordered before the post.
- Receipts are per (message, reader). `_FIELD_TYPES` has no map type (`boardlog.py:137`), so they
  have to be one record per read, as `boardlog.Delivered` already is: one record per read,
  carrying ids (`boardlog.py:388-399`). [read]

**Facts that stop being single-valued**
- *read*: per reader.
- *answered*: per reader. Is a needs-answer "answered" when any one member replies, or when all
  do? That is a semantics question the code cannot settle. [inferred]
- *the needs-answer count* on a card: per reader.
- *the wake target*: N−1 `SessionWoken` per send.
- *`decisions_readable_by`*: membership lookup. It is a set already under the core, so no extra
  cost here.
- *NEEDS YOU rows*: one per (message, reader that still owes), or one row naming several.
  `DecisionOwed.key` is per record id (`model.py:737-739`) and badges count per node
  (`ui/rail.py:356-366`), so a per-reader row needs a key of message + reader. [read]

**What the log format would freeze**
- the recipient-set shape on the message record;
- the membership-change records, and their meaning: does a joiner read the history?
- the per-reader receipt kind.

**UI**
- The card count is per reader, so the same message counts on several cards.
- The DIGEST thread appears in N digests.
- NEEDS YOU may hold several rows per message.

**Rough size: M** if membership is fixed at link time. **L** if members can join or leave, because
join and leave touch who owes what, who may read history, and the restore check. [inferred]

### 2.2 O2: pairs only; N>2 through coordinator leads; forward by reference

**New records and fields**
- a `Forwarded` intent and record: the original message id, onto channel Y, by coordinator C;
- `forwarded_from: tuple[str, ...]` (a provenance chain, which fits `_FIELD_TYPES`) on the new
  message, or a separate forward record;
- a tool, `forward_peer_message`, added to `_REVIEW` and to the three pinned tool lists.

**Facts that stop being single-valued**
- Per message, read / answered / wake target all stay single, because each hop is a pair message.
- **Who may read a message stops being "the channel's two members".** Forwarding *by reference*
  lets B read a message on channel A–C, which B is not linked to. Proposal item 1 says "none can
  see a session it isn't linked to: discovery is the link" (two-leads plan, lines 55-56). A
  forward is a deliberate exception to that rule, and readability then has to follow forward
  edges, not membership. [read for the rule; inferred for the cost]
- *answered* for the original asker becomes a chain: A's needs-answer to C is answered when C
  replies. Whether C's reply has to carry B's answer is convention, not code.
- *`decisions_readable_by`*: Decided 3 reaches the pair only. A decision on A–C is not readable by
  B unless decisions can also be forwarded. The operator's ratification of Decided 3 was "if this
  makes N>2 team coordination easier" (two-leads plan, line 126). Under O2 it does not, beyond one
  hop. [read for the quote; inferred for the consequence]

**What the log format would freeze**
- The forward record and the chain's shape.
- Under fact 1 the forward lives in the *target* channel's log while it references a message in
  another channel's log. A restore that has one channel and not the other then holds a dangling
  reference. The existing precedent reports the dangling id rather than refusing: `Concern.task_id`
  is not validated (`model.py:1547-1553`). [read]

**UI**
- Card counts stay per pair.
- A forwarded message needs its chain drawn ("from A via C").
- DIGEST threads stay per pair.

**Gate cost**
- Each hop is a send on the forwarder's rung. On reviewed rungs, A→C→B is **two** operator
  approvals for one request, where O1 needs one.
- This follows from Decided 1 (`approval.py:233` releases the whole review list only on
  AUTONOMOUS). [inferred]
- **Revised (S1 finding 4; R5, read).** The extra park holds only when C is on a reviewed rung.
  Putting `forward_peer_message` in `_REVIEW` follows the existing rule rather than a new choice:
  the `_REVIEW` comment says a message between agents is reviewed because "it changes what
  another agent does next", and a forward chooses who acts. The operator can avoid the park by
  putting C on AUTONOMOUS, but `_AUTONOMOUS_AUTO = _REVIEW` releases C's whole review list
  (its `Edit`, `Bash`, `Agent` too), so that is free only for a coordinator that does no work of
  its own. A per-tool release for forwards would be new mechanism.

**Rough size: M.** The forward record and tool are S. The readability widening and its
restore-time dangling case are M. [inferred]

### 2.3 O3: pairs with an enforced direction (parent/child)

**New records and fields**
- O2's, plus a direction on the channel record.
- A refusal at link time (`ChannelLinked` refused on a second parent or a cycle), with a refusal
  enum like `DetachRefusal` (`model.py:1152`).
- Siblings still reach each other only through the parent, so O3 needs O2's forward unless N>2 is
  only ever parent↔child. [inferred]

**Facts that stop being single-valued**
- Same as O2.
- What the direction removes is cycles: the wake-loop exposure under fact 3 is bounded by tree
  depth.

**What the log format would freeze**
- A direction field on the link record.
- Under "the format only grows" (`boardlog.py:58-61`), a direction field can be **added later**
  with a default that claims nothing. So O2 → O3 is an additive format change, and O3 → O2 means
  ignoring a field. [read for the rule; inferred for the application]
- **Revised (S1 finding 13; R5, read).** The same rule says "a build older than a field ignores
  the key" (`boardlog.py`, module docstring, "The format only grows"). So a direction added later
  is silently ignored by an older build, which then reads a directed link as an undirected one
  and enforces nothing. Adding it later is format-safe but not behaviour-safe. A new record kind
  is the only widening an older build notices (it reads as `Unrecognised` and the board goes
  short).

**Strains a decided item**
- Proposal item 1 makes the charter "what each side may ask of the other" (two-leads plan,
  line 54).
- "The charter flows down" makes it one-directional: what may a child ask of its parent?
- Decided 1 ("the rung belongs to the board it protects", line 120) is not affected.

**UI**
- O2's, plus the direction on the card.
- The Link leads dialog has to say which side is the parent, and why a link was refused.

**Rough size: S on top of O2.** One field, one refusal check, one dialog choice. [inferred]

### 2.4 O4: pairs plus fan-out ("ganging")

**New records and fields**
- A `Gang` record: an operator-named set of pair channels.
- One send produces K pair messages that share a `send_id`. Each message is an ordinary pair
  message.
- What a gang means when one of its channels is unlinked is not settled.

**Facts that stop being single-valued**
- None per message: every copy is a pair message with one reader, one receipt and one wake
  target.
- *answered* "for the send" becomes the aggregate over the copies. It is derived, as in O1, but
  the question of any reader or all readers comes back.
- *`decisions_readable_by`*: a decision raised "on the gang" needs the gang's member set.
  Otherwise channel decisions stay pair-scoped and Decided 3 helps only within one pair, as in O2.

**What the log format would freeze**
- The gang record and the `send_id` field.
- A gang send logs K copies, so the body is logged K times. With one log per channel (§1) the K
  copies are in K logs. Each record is bounded at 1 MiB (`boardlog.MAX_RECORD_BYTES`,
  `boardlog.py:133`) and bodies at 24,000 characters (`bus.MAX_TEXT_CHARS`, `bus.py:247`), so
  this costs disk, not correctness. [read]

**Gate**
- One tool call means one park. The operator reviews the body once and it reaches K. An edit in
  flight (`EDITED_KEY`, `bus.py:279`) applies to all copies. [inferred]

**UI**
- Card counts are per pair. A gang send shows K rows unless they are grouped by `send_id`.

**Rough size: S–M.** The pair machinery is unchanged. The gang record, the fan-out arm, and
gang-scoped decisions (if wanted) are the delta. [inferred]

### 2.5 O5 (added by me): a shared board instead of messages, noted and not costed

Teams coordinate by declaring into each other's boards (a cross-session task with provenance)
rather than by messaging. I am adding it because proposal item 3 already routes "work a peer
asks for" through `declare_task`. Its known costs, all [relayed]:
- Task ids are fleet-wide.
- A dependency on another session's task "reads as missing and cannot be declared".
- `model.Task.dependency` resolves only on the task's own board, which reverses a pinned test.

(2026-10-12 record, lines 204-205 and 220; I did not check these lines against code.) Fact 1
applies to it in full. I did not cost it further.

---

## 3. Existing invariants each option strains or breaks

Each invariant names its file:line and is [read]. The O1–O4 columns are my reading of the
consequences: [inferred]. Legend: **B** breaks, **S** strains, **–** unaffected or no worse
than the core.

| Invariant | Where | Core | O1 | O2 | O3 | O4 |
|---|---|---|---|---|---|---|
| A fact goes to one session's log; a restore refuses another session's | `boardfacts.py:33`, `:123`, `:435`; `store.py:2729-2737`, `:2992-2993` | **B**: needs a channel-keyed log | – | S: a forward references another channel's log | as O2 | S: K logs per send |
| No cross-session wake, so no wake loop | `store.py:2099-2120` | **B** (by design) | S: one send wakes N−1, a reply-all round is N(N−1) | S: the operator can link a cycle (links are symmetric), so forwards can loop | – beyond core: a tree bounds it | S: one send wakes K |
| Roles resolve within a session; mail is in-session | `driver.py:1456-1468`; `board.py:796-798` | **B**: routing moves to the store | – | – | – | – |
| A wake for an absent session is dropped | `app.py:1124-1150` | **B** for Decided 4 | N sessions to re-wake | – | – | – |
| `needs_you` changes only on apply; the reducer reads no clock | `store.py:1431-1440`; STYLE.md:41-50 | **B** for Decided 4's age | – | – | – | – |
| One reader per record: `decisions_readable_by` is own or session root | `model.py:2798-2817` | S: Decided 3 adds a channel branch | – | S: does not reach past one hop without forwarded decisions | as O2 | S: gang scope |
| Decisions are not logged | `model.py:629`; `boardfacts.py:164-175` | S: proposal item 5 vs Decided 3 | – | – | – | – |
| Obligation key per record, badge count per node | `model.py:737-739`; `ui/rail.py:356-366` | S: a sixth kind | S: per (message, reader) | – | – | – |
| A short restore is never adopted; an unknown kind makes it short | `model.py:2627-2640`; `boardlog.py:58-61` | S: an older build reading a channel log stops recording it for good | – | – | – | – |
| "Discovery is the link" (a planning rule, not code) | two-leads plan, lines 55-56 | – | – | **B** if forwarding grants read by reference | as O2 | – |
| One review per tool call | `approval.py:233`; `bus.post_concern` parked as a call | – | one review reaches N | one review per hop | as O2 | one review reaches K |
| Bus tool names are pinned in three lists | `tests/test_bus.py:1106-1136`, `:1391`; `tests/test_templates.py:328-330` | S | – | +1 tool | +1 tool | +1 tool or parameter |

**Revised (S1 finding 1; R5, run).** The first row's Core cell ("B: needs a channel-keyed log")
holds for the link record and decisions only. Messages filed per sender do not break the
invariant (see fact 1's revision). Read per option, the first row becomes: O1 S (a thread over
N senders' logs, whole only when all N are resumed; a multi-recipient record is a new kind);
O2/O3 – for messages (a pair thread spans 2 logs), S for a forward that references another
pair's message; O4 – (each copy is a single-recipient record in the sender's log, not "K logs").

---

## 4. What I could not establish by reading

1. **Whether a channel log can be restored with no session resume driving it.** Today the only
   restore trigger is `spec.resume` in `app._launch` (`app.py:2460`). I did not read
   `app._settle_restore`, `_open_board_log`, or the `BoardWriter.adopt` lifecycle closely enough
   to say whether a second key type (channel) fits, or needs a parallel writer state.
   `boardsync.py` says "a process holds one" writer (`boardsync.py:66-69`).
2. **Whether proposal item 5 means channel decisions must persist.** Fact 6 is a contradiction
   between two planning items, and the tree cannot resolve it. It is flagged here and not settled.
3. **What "answered" means for a needs-answer that has more than one reader** (O1, O4 aggregate).
   It is a semantics choice. The code only shows that any version of it can be derived, not
   stored.
4. **Whether a model would forward, reply-all, or loop.** This is a behavioural question that
   cannot be read. The two-leads plan says the same of every channel behaviour ("unknown until one
   is built and run", line 152-154).
5. **The draw-time cost** of N per-reader rows or K fan-out copies. `store._owing_decisions`
   already measures about 275 µs per intent with 16 decisions open, most of it in
   `board.role_name` (2026-10-12 record, line 295-296) [relayed, not re-measured]. A sixth
   obligation kind built the same way would add to it.
6. **requests.py** (the deferred cross-process channel) has a single `Request.to` (`requests.py:242`)
   and per-reader receipts already in the reader's own directory. It is unwired (grep found no
   importer [read]), so its unversioned digest (`requests.py:448-452`) freezes nothing until a
   first real record. I did not cost the topologies against it, because the operator deferred
   inter-process work.
7. **Files I did not read beyond grep and the cited ranges:** `intents.py` bodies, `effects.py`
   apart from wakes, `ui/inbox.py`, `ui/detail.py`, `ui/fleet_view.py`, `ui/session_menu.py`,
   `app._settle_restore`, `boardlog.restore`, and every test body.
