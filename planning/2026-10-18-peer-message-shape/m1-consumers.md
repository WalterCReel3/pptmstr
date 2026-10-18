# M1: every Concern consumer, classified by how a peer message would differ

Measurement for the 2026-10-17 plan's Decided 8 ("under measurement"). It answers one question: how many
of today's concern sites would a lead-to-lead message change, and how many of those changes
would a site have to remember to make. **There is no recommendation here.** A separate
judgement task comes next.

Tags: **(run)** means I executed it and I'm reporting the output. **(read)** means I established it by reading the code.

> **Revised (S4 findings 1–13; M2).** S4-shape-skeptic's findings are settled in
> [`m2-judgement.md`](m2-judgement.md) §(a). The corrections are marked in place below as
> "Revised (S4 finding N; M2)", with the M1 text kept (struck through where it is replaced).
> The M1 counts are no longer the counts the judgement uses: §(b) of m2-judgement.md has the
> corrected ones, for three designs (K, H, P) rather than two.

## Method

1. **Finding sites (run).** `m1_sites.py` is stdlib and AST-based. I ran it as
   `python -B -I planning/2026-10-18-peer-message-shape/m1_sites.py <root> [--tests]` against this
   worktree at `e57b027`.
   - A *site* is the innermost function, method or class body that holds a match, or the
     module-level statement when there is none.
   - Every `case` arm of a `match` is a site of its own, because adding a record kind adds an
     arm, and editing one arm doesn't touch its neighbours.
   - The script lists what counts as a match in its docstring: types, intents, effects, boardlog
     record classes, `.concerns`/`.concern_id`/`.incident` fields, names spelled with `concern`,
     and the tool-name strings. Docstrings and comments never match.
   - **Results.** `pptmstr/` has **137 sites**, and `tests/` has **367** (504 with `--tests`). 27
     and 62 statements that are only imports are left out of those counts.
   - **Revised (S4 findings 2, 3 and 11; M2) (run).** `m1_sites.py --m2` adds the prose
     strings and briefing names S4 showed the script could not see, and counts boardlog record
     names inside `boardlog.py`. Without `--m2` the output is byte-identical to the M1 run
     (checked with `diff`). With it, `pptmstr/` has **143 sites** and `pptmstr/`+`tests/` **546**.
     The six new production sites are `boardlog.py:454` (the `Known` union), `boardlog._named`'s
     two arms (`:921`, `:927`), `driver.AgentSession._system_prompt` (`:2340`, calls
     `lead_briefing`) and `templates._concern_audience_lines`' two arms (`:214`, `:219`). Of the
     36 new test sites, two pin prose a peer message changes (`test_wake_asker.py:484`,
     `test_templates.py:718`); the rest call `lead_briefing` about other things.
   - **Two false positives were removed by changing the rules, not by hand.** First, "inbox" in
     a definition name meant the operator's NEEDS YOU pane in `ui/inbox.py` and the tests of it,
     so that word was dropped. Second, `decision_view`'s `Withdrawn` belongs to decisions, so a
     boardlog record name now counts only when it is imported from `boardlog` or qualified with
     it.
2. **What the script can't see, and how each gap was covered (read).**
   - **Callees that never spell a concern name.**
     - The driver's addressing is reached from `post_concern`/`read_inbox` through a
       `session` object: `AgentSession.resolve_role`, `role_of`, `role_status`.
     - The gate stamps the sender and the edit flag on every bus call:
       `driver._stamp_bus_call`, `bus._sender`.
     - Wakes are sent by session, with no concern-specific type: `app._send_wakes`.
     - Policy membership is a string set: `approval._REVIEW`, `_AUTONOMOUS_AUTO = _REVIEW`.

     These seven are classified below as "found by reading".
   - **Generic helpers.** Some code receives concern text but is shared with tasks and decisions
     and never names a concern: `_quoted`, `_said`, `_one_line`, `basis_label`, `role_name`,
     `address_in_session`, `typeset` parts. They are identical for any message by construction
     and aren't counted. Where one of them decides something for a peer (`role_name`'s FOREIGN
     qualifier, `role_of`'s None), it is named under the site that calls it.
   - **Projections consumed indirectly.**
     - `BoardTask.concerns` is a tuple of ids: the script finds the attribute reads, but not
       every caller of a function that returns rows.
     - `Digest.incidents` reaches the pane and `read_digest` through `Reported`.

     I followed both chains by hand from `board_tasks`/`board_concerns`/`digest`, through
     `bus._reasons_text`/`_task_lines`/`_part_text`/`_incident_lines` and `ui/board_pane`/
     `ui/digest_pane`, and every consumer on them is in the list.
   - **Dynamic access.** I found no `getattr` on concern fields. Lists of fields and kinds
     exist in two places. `boardlog._KINDS` and `_HINTS` are derived with `get_args(Known)`.
     `tests/test_boardlog.py:2342` walks `dataclasses.fields`. Both are listed, because a
     field added under K is caught there.
   - **Fixture and UI tool-name strings** are found only by the literal
     `mcp__pptmstr__post_concern`. A copy assembled from parts would be missed. I found none by
     grep.
3. **Classifying (read).** I read every production site against the task's rubric, as written.
   - **I:** identical for a peer.
   - **D:** differs for a peer. The kind is one of: authority/standing, wake,
     routing/addressing, obligation, threading, task_id, display, gate/approval, restore/log,
     other.
   - **F:** needs a new field only, the same for concerns and peers.
   - **N:** not reachable by a peer.

   Every D site also says whether K's single derived `is_peer` projection makes it correct
   without the site having to remember to call it (**remember: no**), or whether the site has to
   remember (**remember: yes**).
   - **Premises the classification assumes**, all from the plan doc (read):
     - Under K, peers use the existing `post_concern`/`read_inbox`, with `to` able to name a
       channel. That's the one-mailbox reading K's "two paths" argument implies; the old
       ruling left it open.
     - Only leads send (proposal item 7).
     - A message is filed in its sender's log (Decided 5, 6).
     - Each side's rung governs (Decided 1).
     - The charter is judged at the parked declaration and not at the message (Decided 2).
   - **Correctness has to be measured against something, so this is the bar.** A peer message
     must:
     - stay quoted;
     - never give a peer's text standing as an instruction;
     - wake the recipient lead;
     - be addressable by channel;
     - carry needs_answer/re;
     - never broadcast a peer's text to the recipient's workers.
4. **Counting.** A scratch script joined the run's site list with the hand classification and
   checked that every listed site had a class and that no class pointed at a stale site. That
   script lives in `$TMPDIR`, by the touches rule.

## Counts

| | I | D | F | N | total |
|---|---|---|---|---|---|
| `pptmstr/`, found by the script | 113 | 8 | 10 | 6 | 137 |
| `pptmstr/`, found by reading | 4 | 3 | 0 | 0 | 7 |
| **`pptmstr/` total** | **117** | **11** | **10** | **6** | **144** |
| `tests/` (see "Test sites") | 334 | 2 | 15 | 16 | 367 |

**The 11 production D sites by kind:**

| Kind | Sites | Which |
|---|---|---|
| wake | 3 | `store._concern_wake`, `effects.WakeReason`, `bus.wake_line` CONCERN_TO_LEAD arm |
| routing/addressing | 3 | `bus.post_concern`, `driver.resolve_role`, `driver.role_status` |
| display | 3 | `bus.read_inbox`, `driver.role_of`, `board.board_concerns` |
| task_id | 1 | `board._open_concerns_by_task` |
| authority/standing | 1 | `templates.lead_briefing` |
| obligation, threading, gate/approval, restore/log | 0 | none |

Obligation (needs_answer) and threading (re) differ at **no existing site**. They are new fields
(the F rows), plus consumers that don't exist yet under either design: the needs-answer count on
the card, Decided 4's age rule in NEEDS YOU, and a thread view in DIGEST. Gate/approval and
restore/log differ nowhere for a peer either. They need the fields (F), and the restore path is
already scoped by the sender's session, cross-session recipients included (`store._restored_concern`'s
docstring, and probe (d) below).

**Remember under K: 8 of the 11 D sites must remember. 3 don't.**

- **The 3 that don't.**
  - `effects.WakeReason` and `bus.wake_line`: adding an enum member for a peer wake makes the
    existing `assert_never` in `wake_line` a mypy error until it has an arm. K gets this check
    too, as long as it adds a member rather than reusing `CONCERN_TO_LEAD`.
  - `board._open_concerns_by_task`: it is sender-scoped by construction, so a peer's `task_id`
    can only ever link a row on the sender's own board. That is correct exactly when the rule is
    "a peer message's task_id names the sender's task". It is wrong if a peer is expected to name
    the recipient's task, which Decided 8 calls ambiguous. Either way the site needs no edit.
- **The 8 that must, and what forgetting each one costs.** This is the hazard P's type check
  removes, so the default matters:

| Site | Forgotten under K, the result is | Fails |
|---|---|---|
| `store._concern_wake` | no wake: it already returns `()` when `sender[0] != recipient[0]`. The message waits, and Decided 4's NEEDS YOU age is the backstop | safe, and a test sees it |
| `bus.post_concern` | `to=<channel>` is refused with `role_status` text | loud |
| `driver.resolve_role` | same as above | loud |
| `driver.role_status` | the refusal says "no such role" rather than naming the channel | loud, misleading text |
| `bus.read_inbox` | the peer's message is delivered as `[from another agent, ...]`, still behind `\| `. The recipient lead can't tell a peer from one of its own, and gets no name to reply to | **silent** |
| `driver.role_of` | the cause of the row above (None for any node of another session) | **silent** |
| `board.board_concerns` | the recipient's board doesn't list inbound peer mail, and a non-running peer lead reads "waiting" and not "posted to an agent that is not running" | safe: nothing is broadcast |
| `templates.lead_briefing` | the lead is never told what a peer message is | silent, **and the same under P**: prose has no type |

**So under K, 2 of the 8 are silently wrong if forgotten** (`read_inbox` and the `role_of` it
calls, which are one rendering path), and 1 more is silently wrong under both designs (the
briefing). The other five fail loudly or safely.

> **Revised (S4 findings 1, 2, 3, 4 and 10; M2).** The paragraph above is not the count the
> judgement uses.
> - **Finding 10:** `WakeReason` and `wake_line` are the same edit under every design. They
>   are "same under all", not K "remember: no".
> - **Finding 2 (read, and the script with `--m2`, run):** the `post_concern` and `read_inbox`
>   tool descriptions become false under K only, and fail silently. They are two more K-only
>   silent sites, pinned by `test_wake_asker.py:484`.
> - **Finding 3 (partly):** `_concern_audience_lines`' STRICT arm is D, silent, but under all
>   three designs: the sentence says "Messages between agents", which a peer message is
>   under H and P too.
> - **Finding 4:** the role/channel namespace in `to` is two more K-only silent sites
>   (`resolve_role`, `_address_for`).
> - **Finding 1 (run, mypy):** P's type check does not cover the sites it was credited with.
>   It points at a site only where a `Concern` annotation meets the union; attribute access
>   on shared field names is not flagged, and neither is a separate collection. P's figure is
>   not 0. See m2-judgement.md §(a) item 1.
>
> Corrected K-only silent sites: `read_inbox`, `role_of`, the two tool descriptions,
> `resolve_role`/`_address_for`, plus the older-build misread of finding 6: **7**, against M1's 2.

## The sites (production)

Line numbers are from the run at `e57b027`; the qualified names are the stable reference. The
note column is empty for I rows; the reasons for those are grouped after the table.

| Site | Qualified name | Class | Note |
|---|---|---|---|
| `pptmstr/app.py:2127` | `_newest` | I |  |
| `pptmstr/app.py:2144` | `_addresses_of` | I |  |
| `pptmstr/approval.py:125` | `<module>` | I |  |
| `pptmstr/approval.py:352` | `<module>` | I |  |
| `pptmstr/approval.py:1087` | `summarize` | F | queue row would show needs_answer/re like basis and incident |
| `pptmstr/board.py:138` | `incident_marker` | I |  |
| `pptmstr/board.py:181` | `BoardTask` | I |  |
| `pptmstr/board.py:374` | `BoardConcern` | F | BoardConcern carries channel/needs_answer/re |
| `pptmstr/board.py:658` | `board_tasks` | I |  |
| `pptmstr/board.py:767` | `_open_concerns_by_task` | D task_id, remember: no | sender-scoped: a peer's task_id can only link a row on the sender's board; correct iff that is the rule. **Revised (S4 finding 9; M2):** a rule choice, not a measurement; the judgement fixes the rule as "a peer's task_id names the sender's task" (m2-judgement.md §(d)) |
| `pptmstr/board.py:787` | `board_concerns` | D display, remember: yes | sender-scoped: recipient's board never lists inbound peer mail (fail-safe, no broadcast); a non-running peer lead reads 'waiting', not undeliverable |
| `pptmstr/board.py:851` | `incidents` | I |  |
| `pptmstr/boardfacts.py:111` | `<module>` | I |  |
| `pptmstr/boardfacts.py:273` | `outcomes.[case TaskClosed()]` | N | operator's closing notice |
| `pptmstr/boardfacts.py:368` | `outcomes.[case ConcernPosted()]` | I |  |
| `pptmstr/boardfacts.py:374` | `outcomes.[case ConcernEdited()]` | I |  |
| `pptmstr/boardfacts.py:380` | `outcomes.[case ConcernWithdrawn()]` | I |  |
| `pptmstr/boardfacts.py:386` | `outcomes.[case InboxRead()]` | I |  |
| `pptmstr/boardfacts.py:426` | `_filed` | F | Posted record gains the fields |
| `pptmstr/boardlog.py:345` | `Posted` | F | record gains the fields; older build drops them silently (claim d) |
| `pptmstr/boardlog.py:367` | `Edited` | I |  |
| `pptmstr/boardlog.py:378` | `Withdrawn` | I |  |
| `pptmstr/boardlog.py:388` | `Delivered` | I |  |
| `pptmstr/boardlog.py:1052` | `RestoredConcern` | F | gains the fields |
| `pptmstr/boardlog.py:1073` | `Board` | I |  |
| `pptmstr/boardlog.py:1161` | `restore` | I |  |
| `pptmstr/boardlog.py:1251` | `restore.[case Posted()]` | F | restore arm copies the fields |
| `pptmstr/boardlog.py:1268` | `restore.[case Edited()]` | I |  |
| `pptmstr/boardlog.py:1280` | `restore.[case Withdrawn()]` | I |  |
| `pptmstr/boardlog.py:1292` | `restore.[case Delivered()]` | I |  |
| `pptmstr/boardlog.py:1389` | `_waiting` | I |  |
| `pptmstr/bus.py:188` | `<module>` | I |  |
| `pptmstr/bus.py:595` | `_board_line` | I |  |
| `pptmstr/bus.py:934` | `_agent_notes` | I |  |
| `pptmstr/bus.py:948` | `_operators_notice` | N | operator's closing notice |
| `pptmstr/bus.py:1029` | `_reasons_text` | I |  |
| `pptmstr/bus.py:1095` | `_unread_note` | I |  |
| `pptmstr/bus.py:1212` | `_delimited_note` | I |  |
| `pptmstr/bus.py:1279` | `_board_text` | I |  |
| `pptmstr/bus.py:1350` | `_task_text` | I |  |
| `pptmstr/bus.py:1420` | `_cuttable` | I |  |
| `pptmstr/bus.py:1430` | `_task_lines` | I |  |
| `pptmstr/bus.py:1530` | `_note_heading` | I |  |
| `pptmstr/bus.py:1549` | `_part_text` | I |  |
| `pptmstr/bus.py:1796` | `_digest_text` | I |  |
| `pptmstr/bus.py:1951` | `_incident_headline` | I |  |
| `pptmstr/bus.py:1973` | `_incident_lines` | I |  |
| `pptmstr/bus.py:2002` | `_withheld` | I |  |
| `pptmstr/bus.py:2019` | `_bounded_digest_text` | I |  |
| `pptmstr/bus.py:2891` | `build_server` | I |  |
| `pptmstr/bus.py:2969` | `build_server.post_concern` | D routing, remember: yes | to resolves within the session only; needs channel addressing, needs_answer/re args, a task_id rule. **Revised (S4 findings 2, 5; M2):** under K its @tool description ("No other concern wakes anyone ... nor one to another session") turns false silently; under every design the only-leads-send refusal lives in the sending handler |
| `pptmstr/bus.py:3039` | `build_server.read_inbox` | D display, remember: yes | sender shown as 'another agent' (role_of is None cross-session); no channel to reply on. **Revised (S4 finding 2; M2):** under K its @tool description ("nothing else wakes you for one") turns false silently too |
| `pptmstr/bus.py:3140` | `build_server.read_board` | I |  |
| `pptmstr/bus.py:3188` | `build_server.read_task` | I |  |
| `pptmstr/bus.py:3996` | `<module>` | I |  |
| `pptmstr/bus.py:4104` | `wake_line.[case WakeReason.CONCERN_TO_LEAD]` | D wake, remember: no; **Revised (S4 finding 10; M2): same under K, H and P** | reusing CONCERN_TO_LEAD would print 'lead sent you a concern'; a new WakeReason member makes assert_never force a new arm |
| `pptmstr/digest.py:226` | `Reported` | I |  |
| `pptmstr/digest.py:242` | `Digest` | I |  |
| `pptmstr/digest.py:268` | `digest` | I |  |
| `pptmstr/digest.py:609` | `incidents_heading` | I |  |
| `pptmstr/digest.py:630` | `incident_attribution` | I |  |
| `pptmstr/digest.py:652` | `incident_label` | I |  |
| `pptmstr/effects.py:81` | `InboxDelivered` | I |  |
| `pptmstr/effects.py:150` | `BoardDelivered` | I |  |
| `pptmstr/effects.py:320` | `WakeReason` | D wake, remember: no; **Revised (S4 finding 10; M2): same under K, H and P** | needs a member for a peer wake |
| `pptmstr/effects.py:357` | `<module>` | I |  |
| `pptmstr/fake_driver.py:97` | `<module>` | I |  |
| `pptmstr/fake_driver.py:295` | `<module>` | I |  |
| `pptmstr/fake_driver.py:957` | `FakeDriver._seed_board` | I |  |
| `pptmstr/fake_driver.py:1020` | `FakeDriver._seed_board.post` | I |  |
| `pptmstr/fleetsync.py:257` | `changes.[case SessionDetached() \| FailureAcknowledged() \| T...]` | I |  |
| `pptmstr/intents.py:297` | `ConcernPosted` | I |  |
| `pptmstr/intents.py:311` | `InboxRead` | I |  |
| `pptmstr/intents.py:329` | `ConcernEdited` | I |  |
| `pptmstr/intents.py:344` | `ConcernWithdrawn` | I |  |
| `pptmstr/intents.py:1098` | `<module>` | I |  |
| `pptmstr/model.py:1356` | `<module>` | I |  |
| `pptmstr/model.py:1478` | `stated_incident` | I |  |
| `pptmstr/model.py:1491` | `ConcernState` | I |  |
| `pptmstr/model.py:1506` | `Concern` | F | gains channel/needs_answer/re |
| `pptmstr/model.py:2447` | `is_closing_notice` | N | operator's closing notice |
| `pptmstr/model.py:2458` | `closing_notice` | N | operator's closing notice |
| `pptmstr/model.py:2562` | `Restore` | I |  |
| `pptmstr/model.py:2644` | `Snapshot` | I |  |
| `pptmstr/model.py:2730` | `Snapshot.empty` | I |  |
| `pptmstr/model.py:2741` | `Snapshot.inbox_of` | I |  |
| `pptmstr/store.py:377` | `_apply` | I |  |
| `pptmstr/store.py:740` | `_apply.[case ConcernPosted()]` | I |  |
| `pptmstr/store.py:749` | `_apply.[case BoardRead()]` | I |  |
| `pptmstr/store.py:763` | `_apply.[case InboxRead()]` | I |  |
| `pptmstr/store.py:782` | `_apply.[case ConcernEdited()]` | I |  |
| `pptmstr/store.py:787` | `_apply.[case ConcernWithdrawn()]` | I |  |
| `pptmstr/store.py:1047` | `_apply.[case TaskClosed()]` | N | operator's closing notice |
| `pptmstr/store.py:1108` | `_apply.[case BoardRestored()]` | I |  |
| `pptmstr/store.py:2099` | `_concern_wake` | D wake, remember: yes | already returns () when sender[0] != recipient[0]; the lead-to-lead wake is a new branch here |
| `pptmstr/store.py:2592` | `_restored` | I |  |
| `pptmstr/store.py:2973` | `_restored_concern` | F | copies the fields from the restored record |
| `pptmstr/store.py:3025` | `_timeless_concern` | I |  |
| `pptmstr/templates.py:39` | `<module>` | I |  |
| `pptmstr/templates.py:197` | `_concern_audience_lines` | ~~I~~ **Revised (S4 finding 3; M2):** D authority/gate, remember: yes, silent, same under K, H and P | the STRICT/PERMISSIVE arm tells a lead "Messages between agents are reviewed by the operator before they arrive"; a peer from an AUTONOMOUS sender is not (Decided 1). Pinned by `test_templates.py:718` |
| `pptmstr/templates.py:356` | `lead_briefing` | D authority, remember: yes | lead must be told what a peer message is and is not (request between equals, work enters by declaration) |
| `pptmstr/templates.py:578` | `worker_prompt` | N | workers never send or receive peer mail (proposal item 7) |
| `pptmstr/ui/board_pane.py:185` | `<module>` | I |  |
| `pptmstr/ui/board_pane.py:200` | `<module>` | I |  |
| `pptmstr/ui/board_pane.py:230` | `<module>` | I |  |
| `pptmstr/ui/board_pane.py:380` | `draw` | I |  |
| `pptmstr/ui/board_pane.py:538` | `_tasks` | I |  |
| `pptmstr/ui/board_pane.py:551` | `_task_row` | I |  |
| `pptmstr/ui/board_pane.py:970` | `_concerns` | I |  |
| `pptmstr/ui/board_pane.py:1049` | `_typeset_tasks` | I |  |
| `pptmstr/ui/board_pane.py:1066` | `_typeset_task_row` | I |  |
| `pptmstr/ui/board_pane.py:1244` | `_typeset_concerns` | I |  |
| `pptmstr/ui/board_pane.py:1263` | `_typeset_concern_row` | I |  |
| `pptmstr/ui/board_pane.py:1301` | `_concern_headline` | I |  |
| `pptmstr/ui/board_pane.py:1338` | `_note` | I |  |
| `pptmstr/ui/board_pane.py:1472` | `shows_board` | I |  |
| `pptmstr/ui/board_pane.py:1574` | `restore_line` | I |  |
| `pptmstr/ui/board_pane.py:1753` | `flat_concern` | I |  |
| `pptmstr/ui/board_pane.py:1822` | `reasons_marker` | I |  |
| `pptmstr/ui/board_pane.py:1834` | `row_reasons` | I |  |
| `pptmstr/ui/board_pane.py:1962` | `subject_label` | I |  |
| `pptmstr/ui/board_pane.py:1984` | `reason_heading` | I |  |
| `pptmstr/ui/board_pane.py:2034` | `about_label` | I |  |
| `pptmstr/ui/board_pane.py:2048` | `concern_label` | F | label shows needs_answer like basis |
| `pptmstr/ui/board_pane.py:2063` | `participants_label` | I |  |
| `pptmstr/ui/board_pane.py:2067` | `concern_state_label` | I |  |
| `pptmstr/ui/board_pane.py:2103` | `concern_mark` | F | right-edge mark shows 'needs answer' like 'edited' |
| `pptmstr/ui/board_pane.py:2129` | `<module>` | I |  |
| `pptmstr/ui/board_pane.py:2134` | `<module>` | I |  |
| `pptmstr/ui/board_pane.py:2156` | `concern_glyph` | I |  |
| `pptmstr/ui/digest_pane.py:89` | `draw` | I |  |
| `pptmstr/ui/digest_pane.py:485` | `incident_items` | I |  |
| `pptmstr/ui/digest_pane.py:506` | `_incident` | I |  |
| `pptmstr/ui/fleet_view.py:121` | `_kept` | I | **Revised (S4 finding 1; M2):** S4 called this a silent omission under a separate collection. Refuted (read): `fold` uses `_kept` only to keep *sub-agent* cards, and a peer message is root to root |
| `pptmstr/ui/inbox.py:175` | `<module>` | I |  |
| `pptmstr/ui/inbox.py:204` | `<module>` | I |  |
| `pptmstr/ui/widgets.py:40` | `<module>` | I |  |

**Found by reading** (the script can't see these; see Method 2):

| Site | Class | Note |
|---|---|---|
| `driver.AgentSession.resolve_role` (`driver.py:1456`) | D routing, remember: yes | resolves `lead`/`main`/`root` and in-session addresses only |
| `driver.AgentSession.role_of` (`driver.py:1470`) | D display, remember: yes | `node[0] != self.session_id` gives None, so `read_inbox` says "another agent" |
| `driver.AgentSession.role_status` (`driver.py:1510`) | D routing, remember: yes | the refusal text for an address that didn't resolve |
| `driver._stamp_bus_call` (`driver.py:~2175`) | I | stamps FROM_KEY and EDITED_KEY on every offered bus tool |
| `app._send_wakes` (`app.py:1124`) | I | sends by session, and drops wakes for a session that isn't running, which is consistent with Decided 6 |
| `approval._REVIEW` / `classify` | I | `post_concern` parks on STRICT and PERMISSIVE and is released on AUTONOMOUS. That is Decided 1 for the sender's rung |
| `bus._sender` (`bus.py:499`) | I | stamp to NodeId |
| **Revised (S4 finding 4; M2):** `driver.AgentSession._address_for` (`driver.py:1555`) | D routing, remember: yes, **K only**, silent | allocates role addresses (`builder-2`, ...) in the namespace a channel name would share in `post_concern`'s `to`; a collision resolves to whichever lookup goes first |
| **Revised (S4 finding 5; M2):** `templates.BUS_TOOL_NAMES` / `Role.tool_list` (`templates.py:39`, `:104`) | D authority, remember: yes, same under K, H and P, silent | every worker is offered the bus tools, and a role with `tools=None` inherits all of them, so "only leads send" needs a `sender[1] is None` refusal in whichever handler sends to a peer |

**Why the I rows are I**, grouped (read):

- **Scoped by the sender's session, which is already right for a peer.**
  - Each of these keys on `concern.sender[0]`, and a peer's filing belongs to its sender
    (Decided 5, 6), so the existing key is the right one:
    - `board.board_concerns`/`_open_concerns_by_task` (see the D rows for what that leaves out);
    - `board.incidents`;
    - `digest.digest`;
    - `boardfacts._filed`/`Edited`/`Withdrawn` arms;
    - `store._restored`'s `STORE_ONLY` sweep;
    - `store._restored_concern` (`NOT_THIS_SESSIONS`).
  - `boardfacts.outcomes`' `InboxRead` arm already splits one read's delivery across senders'
    sessions ("One read can hand over concerns from more than one sender's session"), with a
    test, `test_boardfacts.py:1030`.
- **Already cross-session-aware in display.** `board.role_name` qualifies a node of another
  session as `, from another session` (FOREIGN). So every board, task-read, digest and pane line
  that names a participant already says "lead -> lead, from another session" for a peer:
  - `_note_heading`, `_reasons_text`;
  - `incident_attribution`;
  - `participants_label`, `concern_label`;
  - `_withheld`.
- **Quoting and broadcast rules that hold as written.**
  - **Bodies.** Every agent-facing body goes through `_quoted` behind `| `.
  - **Subjects.** Every subject goes through `_said`.
  - **What a board read broadcasts.** It shows only notes that named a task on the board and
    weren't withdrawn (`_agent_notes`, `_reasons_text`, `_incident_lines`, `_withheld`). For a
    peer, that board is the sender's, so a peer's text never reaches the recipient's workers
    through a board read or a digest.
- **Plumbing with no decision in it:**
  - the intent and effect types and unions;
  - `Snapshot.concerns`/`inbox_of`;
  - the `_apply` arms for posting, reading, editing and withdrawing;
  - fleetsync's no-op arm;
  - `fleet_view._kept`;
  - `app._newest`/`_addresses_of`;
  - the tool registrations and `BUS_TOOLS`;
  - the pane constants and glyph tables;
  - the fake-driver fixtures.
- **Prose-argument tables and gate probes keyed on the tool name**: `ui/inbox.DESCRIBED`,
  `_POST_CONCERN`, `ui/widgets._GATE_PROBES`, `approval._BUS_POST`. Under K the tool is the
  same tool. `DESCRIBED` also shows "any argument named in neither place ... after these", so
  `needs_answer`/`re` reach the approval card with no edit.
- **Two arms with no production emitter (read).** No production code emits `ConcernEdited` or
  `ConcernWithdrawn`: grep finds only `fake_driver.py:1180` and the arms themselves. The real
  edit in flight is the gate rewriting the arguments before the handler runs
  (`driver._stamp_bus_call` sets `EDITED_KEY`), and a refused parked call is never filed. So
  their `_apply`/`boardfacts`/boardlog arms are identical for a peer, and also unreachable in
  production for any message.
  **Revised (S4 finding 8; M2) (run).** `git log -S "ConcernEdited(" -- pptmstr/` and the same
  for `ConcernWithdrawn(` list `eccaa24`, `135f917` and `a6b3dea`. In each, the added lines are
  `case` arms or union members, except `135f917`, which added the `fake_driver` emitter. No
  production emitter has ever existed, so no real log holds those records except one written
  under the fake driver.

**N:**

- the operator's closing notice: `model.closing_notice`, `is_closing_notice`,
  `bus._operators_notice`, `store._apply` TaskClosed, `boardfacts` TaskClosed;
- `templates.worker_prompt`. Workers don't send to or receive from peers.

## Test sites

367 sites, from the same run. Tests pin production behaviour, so I classified each one by the
production behaviour it asserts (read, by test name, and by body where the name was ambiguous).
**This is coarser than the production classification.** I read each test that sounded
cross-session, wake-related or field-enumerating; the remainder were taken from the name.

- **D (2):** **Revised (S4 findings 2 and 3; M2): D (4)**, adding `test_wake_asker.py:484 test_what_still_wakes_nobody_is_said` (pins "No other concern wakes anyone" in bus source: K only) and `test_templates.py:718 test_an_unattended_lead_is_not_told_a_person_reads_its_messages` (pins the audience line: all designs). `m1_sites.py --m2` finds both (run).
  - `tests/test_wake_asker.py:148 test_no_other_concern_wakes_anyone` (wake). Its `other-lead`
    case, `(("s2", None), LEAD)`, asserts exactly what a linked peer wake reverses.
  - `tests/test_templates.py:835 <module>` (other). It is the golden text of a lead briefing,
    and changes with `lead_briefing`.
- **F (15).** Field and kind enumerators and generators that a new field has to reach:
  - `test_boardlog.py`: 102, 228, 293, 2284, 2300, 2320, 2328 and 2342. The last is the
    field walker that fails on a `Concern` field no record restores.
  - `test_boardfacts.py`: 174, 435, 509, 716.
  - `test_bus.py`: 3475, the optional typed arguments, and 5462, the largest record fitting
    the log.
  - `test_model.py:335`.
- **N (16).**
  - Closing-notice tests:
    - `test_boardfacts.py`: 1122, 1163;
    - `test_bus.py`: 7117, 7148, 7188;
    - `test_model.py:598`;
    - `test_store.py`: 4416, 4462, 4466, 4559.
  - Worker-prompt tests: `test_templates.py` 355, 405, 1108.
  - Decision and holder tests that match only on prose: `test_bus.py` 5982, 6116, 6987.
- **I (334).** Everything else. Some of these pin the sender-scoping a peer relies on and
  stay true as written: `test_board.py:625`, `:827`, `:423`; `test_store.py:3930`;
  `test_boardfacts.py:1016`, `:1030`.
  - **Revised (S4 finding 9; M2) (read).** S4 said `:423` and `:827` become D under a
    recipient-task rule. Partly refuted: both tests post *same-session* mail (`QA_1 -> LEAD_1`,
    `DEV_2 -> LEAD_2`), so a rule scoped to peer messages leaves them I. Only a global rule
    ("task_id names whichever board holds it", for every concern) flips `:423`.
  - **Revised (S4 finding 12; M2).** 334 is an upper bound: two D tests were missed (above).

Under P, the I tests that pin behaviour both kinds share would each need a twin for the peer
kind, or a parametrisation over the two, to keep the parallel pinned (STYLE section 3, which
Decided 8 cites). Under K they cover peers as written, wherever a peer is a Concern. Neither
number is measured here.

## The factual claims

### (a) Is every concern quoted behind `| ` wherever it reaches an agent? (read)

**Bodies: yes, on every path. Subjects: no. They are quoted between double quotes (`_said`),
not behind `| `. One body is deliberately behind a different mark.** These are the paths
by which a concern reaches an agent:

| Path | Body | Subject | Sender |
|---|---|---|---|
| `read_inbox` (`bus.py:3039`) | `_quoted(..., "")`: `\| ` | `_said` | `role_of(...) or "another agent"` (tool-made, not agent text) |
| board read notes (`_reasons_text`, `bus.py:1029`) | `_quoted(..., _NOTE_LEAD)`: `\| ` | `_said`, via `_delimited_note` | `_one_line(role_name)` |
| `read_task` notes (`_task_lines` / `_part_text`, `piece`/`alone`) | `_quoted`: `\| ` | `_said`, via `_delimited_note` in `_note_heading` | as above |
| `read_digest` incidents (`_incident_lines`) | `prose(..., _QUOTE)`, and only when `on_board` | `_said`, and only when `on_board` | as above |
| wake (`wake_line`) | none | none | `role_name` only |
| closing notice (`_operators_notice`) | **`_ANSWER` mark, not `\| `** | none, the tool's own heading | "the operator, not from an agent" |

- The closing notice is the operator's text, and `_operators_notice` says so in so many words.
  That's correct and is not a gap.
- `post_concern`'s own reply echoes `to` bare ("Concern delivered to {to}"), and `role_status`
  echoes it as `{key!r}`. Both go back to the agent that wrote it, so no other agent's text is
  involved.
- **What this means for the claim.** K's claim is right for bodies. For subjects it is right
  in substance (quoted, never bare) but not literally (`"..."`, not `| `).

### (b) Does `_concern_wake` already branch on the sender–recipient relation? (read)

**Yes.** `store._concern_wake` returns `()` for
`sender[1] is None or recipient[1] is not None or sender[0] != recipient[0]`: anything but a
worker of the same session addressing its root. It then returns `()` again for
`is_operator(sender)`.

So a peer message today falls through to no wake, and `test_wake_asker.py:148` pins exactly
that for the `other-lead` case.

**Size of a peer branch: about 13 lines, the same under K and P.** I sketched it and did not
run it:

- 1 line: the call site in `_apply`'s `ConcernPosted` arm passes `snap`, because the link lookup
  needs it. That signature change is mypy-forced.
- About 7 lines inside `_concern_wake`: `if sender[0] != recipient[0]:`, then the test that both
  are roots and linked, then a black-formatted `SessionWoken(...)` with a new reason, then
  `return ()`.
- 1 line: a `WakeReason` member.
- 3 or 4 lines: the `wake_line` arm that `assert_never` forces.

The undecided lead-to-lead loop rule (the old ruling's "under the loop rule still to be
decided") is not in that count. Under P the same lines would be in a `_peer_wake`.

### (c) Is `sender[0] != recipient[0]` a reliable peer test? (read)

**Not on its own. It is reliable once two conditions are added, and fragile in one log
shape.**

- **The operator as sender (`is_operator`): reliable.** `closing_notice` is the only
  operator-sent concern. Its sender is `operator_party(task.declared_by[0])` (or `holder[0]`)
  and its recipient is the holder. Claims never cross sessions (`store._pick_claim`, and
  `board_tasks`' docstring), so the two always share a session and the test says not-peer.
  That is correct.
- **Store notices: reliable.** The only `Concern(...)` constructions in `pptmstr/` are
  `bus.post_concern`, `model.closing_notice`, `store._restored_concern` and the fake driver
  (grep). There is no other store-made notice.
- **Sub-agents of another session: wrong without `sender[1] is None and recipient[1] is
  None`.** A sub-agent of session B writing to A's lead passes the test, and it isn't a lead
  message. `resolve_role` makes that unreachable today, but `test_wake_asker.py:148`'s
  `other-sessions-worker` case shows the store is meant to handle it. With K's stated default
  ("peer-standing unless both ends share a session") the error is fail-safe: a cross-session
  sub-agent message gets the stricter standing.
- **Parties restored from an earlier run: reliable for logs this writer produces, and silently
  wrong if `recipient_id` is missing.**
  - `restored_party(session_id, address)` and `node_of` keep a restored sender in the log's own
    session, which `_restored_concern` enforces (`NOT_THIS_SESSIONS`). A peer recipient comes
    back as its real node from `recipient_id`: probe (d) restored `('A', None) -> ('B', None)`.
  - `boardfacts._node` writes `recipient_id` for every node that isn't address-only, and a lead
    never is.
  - **The fragile shape.** If a `concern-posted` record's `recipient_id` is absent or isn't one
    or two elements long, `boardlog._party` falls back to the address string. `store._restored`'s
    `node_of` then maps `"lead"` to `(session_id, None)`, the **sender's own lead**. A peer
    message would restore as same-session mail, and the test would say not-peer, with nothing
    reported. That needs a hand-edited log or one from a foreign writer, and this store never
    writes one.
  - **Revised (S4 finding 7; M2) (read).** This does not weigh for P: a P restore arm modelled
    on the concern one calls the same `node_of`. The guard ("a peer record with no
    `recipient_id` is `NotPlaced`") is the same one line under every design.
- **Link lookup.** A test of "linked now" fails for a link that has ended or wasn't restored.
  K's default (cross-session means peer-standing) doesn't depend on the link, so standing
  survives a missing link. Waking and addressing do depend on the link, and fail closed.

### (d) What does this build's boardlog do with a `concern-posted` record carrying an unknown key? (run)

I ran `$TMPDIR/m1d/probe_d.py` with `/home/wreel/Source/pptmstr/.venv/bin/python`, with
`PYTHONPATH` set to this worktree and `PYTHONDONTWRITEBYTECODE=1`. It used the real
`boardlog.decode`/`restore`, and the on-disk path `append` -> `read` -> `restore_log` in a temp
directory. Output, verbatim:

```
decode[widened]: Posted(at=1760000000.0, concern_id='c-1', sender='lead', recipient='lead', subject='s', body='b', task_id=None, basis=None, incident=False, sender_id=('A',), recipient_id=('B',))
decode[new kind]: Unrecognised(at=1760000000.0, kind='peer-message-posted', text='{...}')
restore[widened]: short=False concerns=1 unrecognised=0
   concern c-1: sender=('A', None) recipient=('B', None)
restore[new kind]: short=True concerns=0 unrecognised=1
disk[widened]: written=Written short=False concerns=[('c-1', ('A', None), ('B', None))] unrecognised=[]
disk[new kind]: written=Written short=True concerns=[] unrecognised=['peer-message-posted']
```

(`text='{...}'` is abbreviated here. The run printed the whole record.)

- **The widened record.** `channel`, `needs_answer` and `re` were dropped without a word. The
  record decoded as an ordinary `Posted`, and the board restored **not short**, holding a plain
  concern from A's lead to B's lead.
- **The new kind.** It was carried as `Unrecognised`, and the board restored **short**.
- **What a widened record would then do in the older build (read).** Its `read_inbox` would
  deliver the message to B's lead as ordinary mail, with no needs_answer and no thread.
- **So P's factual premise holds as stated.** This matches `decode`'s docstring and the
  module docstring ("a build older than a field ignores the key").
- **Revised (S4 finding 6; M2) (run).** Every pptmstr build on this machine reads and writes
  the same logs, so the older-build case is routine. `settings.config_dir()` and
  `sessionfile.root()`, imported from this worktree, the main checkout, `ui-rework`,
  `feat-packaging` and `feat-hybrid-permissions`, all print `/home/wreel/.config/pptmstr` and
  `/home/wreel/.config/pptmstr/sessions` (XDG_CONFIG_HOME unset). `boardsync`'s own docstring
  names the case: "a build that predates the move resuming a session this build started".
- **Revised (S4 finding 6; M2) (run): the downstream half.** `$TMPDIR/m2/hplus/probe_hplus.py`
  restores a widened `concern-posted` followed by its `concerns-delivered`, and the same
  message under a new kind, with this build's `boardlog.decode`/`restore`:

  ```
  widened ['Posted', 'Delivered']
    short=False concerns=[('c-1', <ConcernState.DELIVERED: 'delivered'>)] unrecognised=0 stray=[]
  new kind ['Unrecognised', 'Delivered']
    short=True concerns=[] unrecognised=1 stray=['Delivered']
  ```

  The widened one restores as an ordinary delivered concern, with nothing reported. The new
  kind restores short, and an older build cannot deliver a message it never placed, so it
  can't write the `Delivered` that would start Decided 6's obligation for a reader that never
  saw `needs_answer`.

### (e) What would a peer get for free under K and have to re-plumb under P? (read)

| Surface | Under K (a Concern) | Under P (a new kind), what has to be added |
|---|---|---|
| Approval card | free: `ui/inbox.DESCRIBED[_POST_CONCERN]` makes `body` prose and editable, and new arguments are shown anyway | a `DESCRIBED` entry, a pinned name string in `ui/inbox.py`, plus `test_inbox_typeset.py:207`'s pin |
| Queue row | free: `approval.summarize`'s `_BUS_POST` arm ("message {to} (basis): INCIDENT subject"); F to show needs_answer | an arm for `send_to_peer`, and its name constant pinned to `bus.qualified` |
| Gate parking | free: `post_concern` is in `_REVIEW`, and `_AUTONOMOUS_AUTO = _REVIEW` | add the name to `_REVIEW` (AUTONOMOUS then follows by identity), and to `bus.BUS_TOOLS` or it reaches its handler unstamped |
| Edit in flight | free: `EDITED_KEY` is stamped on every bus call, and `post_concern` reads it | 1 line in the new handler; the stamping is generic |
| DIGEST | free for **incidents** in the sender's digest (`digest.incidents` reads `board_concerns`); a peer *thread* in DIGEST is new under both | `Digest` slot and `Reported` take a union, or there is a peer slot, and `bus._incident_*` and `digest_pane._incident` follow |
| Board notes | free on the sender's board through `_open_concerns_by_task`, if task_id names the sender's task | either none (P keeps peers off boards) or a second projection and pane section |
| Incident marks | free: `incident_marker` in `summarize`, `read_inbox`, board read, pane and digest | each of those, for the new type |
| Basis display | free: `basis_label` at the same places | each of those, for the new type |
| Log and restore | F only: three fields on `Posted`/`RestoredConcern` and the restore arm copies them; `test_boardlog.py:2342` fails until they're listed | a record kind, `Known` member, `restore` arm, `boardfacts.outcomes` case and test generator, which the boardlog docstring requires for a new kind; plus `store._restored`'s placement loop |
| Undeliverable / earlier run | partial: a non-running peer *lead* is never `of_earlier_run`, so it reads "waiting" under both | same |

## What wasn't measured

- The size of P in sites. That depends on which surfaces from (e) a peer kind gets, and that
  hasn't been decided.
- Whether a model uses either shape well. The plan's "Not measured" section applies here.
- Any code path under P or K, since nothing was built. The 13-line wake estimate is a sketch
  I didn't run.
- Forked sessions. I didn't examine whether a fork's new session id makes `sender[0] !=
  recipient[0]` true for mail between a parent and its fork.
