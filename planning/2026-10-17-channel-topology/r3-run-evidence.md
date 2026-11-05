# R3: what the 2026-10-17 file-protocol run measures

**Dated:** 2026-10-17 · **Part of:** the channel-topology research (Q5 of
[`../2026-10-17-two-leads-in-one-window-share-a-channel.md`](../2026-10-17-two-leads-in-one-window-share-a-channel.md)).
Evidence only; no option is recommended here.

**Sources, read only:**
- pptmstr side: `/home/wreel/.pptmstr/worktrees/pptmstr/ui-rework/shots/lander/outbox/` (the rules are in
  `../PROTOCOL.md`);
- brand side: `/home/wreel/holond-brand/.concerns/`, the folder PROTOCOL.md names as the brand lead's outbox. It also
  holds the brand session's builder results and the pre-protocol handoff `pptmstr-copy--lead--handoff.md`.

**How it was measured (run):**
`python3 -I r3_count.py <outbox> <.concerns>`, run twice at 17:25 EDT with byte-identical output. The clock is each
file's mtime, never the time in its name. Message classes and parties come from a table in the script that I set by
reading every message (basis: read). The script prints a mechanical keyword count beside that table, so you can see
where the two disagree. The full output is in the appendix.

Tags: **run** = from the script's output; **read** = from reading the messages or the plan; **inferred** = my
reasoning from those two.

## 1. Counts, latency, crossings

### Count (run)

| | protocol messages | needs-answer: yes | by kind |
|---|---|---|---|
| pptmstr-lead | 23 | 10 | 7 ack, 7 delivery, 6 reply, 3 request |
| brand-lead | 18 | 5 | 10 reply, 5 request, 3 delivery |
| **total** | **41** | **15** | first 12:07:24, last 16:00:41, a span of 233 min |

There is also one pre-protocol message, the brand lead's handoff, written at 09:47:29. pptmstr's first reply names
it at 12:07:24, 2 h 20 min later. That was before any watcher ran, so the latency figures leave it out.

**This corrects the plan's "35 messages … 21 from the pptmstr lead, 14 from the brand lead" (run).** 21 and 14 are
exactly the counts up to 15:04:15. Six more messages followed, up to 16:00:41. The plan counted mid-run, so its
count was not wrong when it was taken. The run was also 3 h 53 min long, not "about three hours".

**Non-message files during the run (run).** 11 builder `*--result.md` files landed in the brand lead's folder
between 12:42 and 15:40. These are the false-wake source behind the plan's failure 3. Nothing but messages landed
in the pptmstr outbox.

### Name stamps against the real clock (run)

| side | name minus mtime: min / median / max |
|---|---|
| pptmstr-lead | −127 / +65 / +122 min |
| brand-lead | −6 / 0 / +6 min |

39 of the 41 positions differ between name order and mtime order. The plan says the brand lead's names "were
right". That holds to within ±6 min, but not for ordering. The brand side named a file `T1355` at 13:52:15, then
`T1353` at 13:53:30. The later file even calls the earlier one "my 13:55 reply" (read). pptmstr's names also run
*behind* the clock: `T1000` was written at 12:07. So "up to 2 h ahead" is half the picture. The error runs both
ways and is about ±2 h.

### Reply latency (run)

All values are in minutes.

| measure | n | min | median | p75 | p90 | max |
|---|---|---|---|---|---|---|
| needs-answer: yes → the first reply whose `re:` names it | 15 | 0.36 | **0.71** | 1.02 | 1.81 | 1.90 |
| any `re:` reply to the other side | 38 | 0.36 | 1.15 | 18.43 | 27.64 | 199.40 |
| of which `ack` | 7 | 0.36 | **0.61** | 0.78 | 0.89 | 0.89 |
| first write by the other side after each message | 39 | 0.06 | 0.98 | 8.98 | 23.48 | 27.30 |
| turn gap (one side's run ends → the other side writes) | 29 | 0.06 | 0.78 | 1.61 | 8.98 | 18.48 |

- ~~**Every needs-answer was answered.** No needs-answer message went unanswered. The slowest reply took 1.9 min.
  The plan's "1–3 minutes" overstates it: the median was 43 s (run).~~

  **Revised (S1 finding 14; R5, run 17:52 EDT, same folders, twice, byte-identical).** The figure above is
  the first reply *of any kind*, and 3 of the 15 were acks (`T1350`, `T1353`, `T1503`). `r3_count.py` now
  also prints the first **non-ack** reply naming each needs-answer:

  | measure | n | min | median | p75 | p90 | max |
  |---|---|---|---|---|---|---|
  | needs-answer: yes → first non-ack reply naming it | 14 | 0.39 | **0.84** | 1.81 | **25.90** | 27.64 |

  - **One needs-answer (`T1350`, the brand side's end-frame ask) has no non-ack reply naming it.** It
    crossed pptmstr's `T1520`; the ack `T1525` said so.
  - `T1353` and `T1503` were work requests. Their first substantive reply was the delivery, 25.9 and
    27.6 min later. The acks at 0.74 and 0.89 min were not answers.
  - So "every needs-answer was answered within 1.9 min" is wrong. The first *acknowledgement* came
    within 1.9 min for every one. The plan's "1–3 minutes" holds for acknowledgements and for rulings,
    not for work requests.
  - Bearing: an "answered" derived as "a reply with `re == id`" (R1 §1) would let an ack discharge a
    needs-answer. Under O2, a coordinator's ack would discharge A's question before B has answered.
    The brand folder had grown since R3's run (builder results only). Protocol counts are unchanged:
    41 messages, none unclassified.
- **The long tail is deliveries, not replies.** A `re:` reply waits on the work it reports: a request at 12:07 was
  delivered at 13:05. The 199-minute maximum is the 15:37 copy note, which re-opens the 12:18 claims thread (read).
- **Polling is in these numbers.** `watch_dir.sh` polls every 20 s by default (read). The interval each side actually
  used was not recorded. So up to about 20 s of each latency may be polling, not the agent's turn (inferred).
- There were 30 turns. One side never wrote more than two messages in a row (run).

### Crossings (run, then read)

The script flags every message whose `re:` does not name the other side's newest message before it. It calls the
flag a likely crossing when the gap is 120 s or less. It found 8 flags, 3 of them inside the window. Reading the
messages settles them:

| written | message | other side's newest | gap | by reading |
|---|---|---|---|---|
| 13:50:58 | pptmstr `T1520` delivery | brand `T1350` request, 13:50:55 | 4 s | **A crossing.** Both were written before either side saw the other. pptmstr's `T1525` says so: "which crossed your message". |
| 13:52:15 | brand `T1355` reply | pptmstr `T1525` ack, 13:51:16 | 59 s | **Not a crossing.** It answers the needs-answer in `T1520`, and the ack in between asked nothing. |
| 13:53:30 | brand `T1353` request | pptmstr `T1530` ack, 13:52:40 | 50 s | **Not a crossing.** It is a new request on the masters thread, and the ack asked nothing. |

The other five flags have gaps of 5–23 min. Each one opens or re-opens a different thread (read).

**The plan says "twice messages crossed without either side seeing it". The mtimes show only one crossing (run +
read).** The second may have looked like a crossing only when the messages were ordered by name. That is not
verified.

## 2. Classification (read; the script counts it)

The five classes from the task are used as given. Each message gets its primary purpose. Many also carry a
needs-answer question, and the header records that separately.

| side | request | answer | approval | status | other |
|---|---|---|---|---|---|
| pptmstr-lead (23) | 4 | 9 | 0 | 9 | 1 |
| brand-lead (18 + handoff) | 6 | 1 | 9 | 3 | 0 |

The roles split by side.
- **brand → pptmstr** carried the work requests and the approvals:
  - the handoff;
  - the v2, v3 and v3-rescope requests;
  - the v4 request;
  - the Hold end-frame request;
  - 9 rulings or approvals.
- **pptmstr → brand** carried:
  - deliveries and answers (9);
  - acks and progress (9);
  - 4 "requests". All 4 ask for a ruling or for information, never for work.

**Work flowed one way in this run: brand to pptmstr (read).** The one "other" is pptmstr's unrequested caveat on
"Spend per session" (`T1545` reply).

Every message, with the line it is classified on, is in the script's `CLASSIFY` table.

## 3. Was this already more than two parties? (read; the script counts it)

**37 of the 42 messages carry a party other than the two leads.** The handoff is counted. Counts are by message, and
one message can name several parties (run, over the reading table):

| party | relayed in | handed on to | messages |
|---|---|---|---|
| founder/operator (one person; see below) | 13 (10 "founder", 3 "operator") | 7 | 19 |
| builders (each lead's own) | 10 (9 pptmstr's, 1 brand's) | 10 (all pptmstr) | 20 |
| an investigator agent (pptmstr side) | 1 | 0 | 1 |

The keyword scan agrees with the reading table except on in-scenario words. In the shots' scenario, "the operator
edits its arguments" and "claimed by builder" are names, not parties. The script lists each disagreement.

**The founder and the operator are the same person (read).** The plan's proposal 4 says it outright: "Both leads are
in the operator's window, so 'the founder said yes (relayed)' doesn't need relaying." The brand lead calls that
person "the founder" and the pptmstr lead calls them "the operator". `T1212` uses both names in one sentence: "The
operator said yes … (relayed: the founder's words)". That one human talked to both leads directly:
- `T1000` ("the operator's go-ahead");
- `T1320` ("the operator asked for the board pane");
- `T1400` ("New from the operator").

The same human's answers were also relayed from one lead to the other.

### Examples, quoted (read)

- **Scope by relayed approval:** 5 requests rest on it. These are the four the plan names and the structure change.
  - `T1212`: "The operator said yes to short looping videos for two panels (relayed: the founder's words in the brand
    session, 2026-10-17, "Yes. Continue with a v2 request for short looping videos.")".
  - `T1324`: "The founder said yes (relayed, 2026-10-17: "Let's do that.")".
  - `T1353`: "The founder has chosen the page's structure (relayed, 2026-10-17): a hybrid of split scroll and
    spotlight."
  - `T1503`: "The founder asked for pre-cut mobile crops (relayed, 2026-10-17: "You may ask the pptmstr lead for new
    crops.")".
- **Decisions already made in a decision pane, then relayed:**
  - `T1537` copy: "The founder answered "3." (relayed, from the founder's decision pane, 2026-10-17)".
  - `T1545` copy: "the founder has chosen pptmstr's tagline … (relayed, from the founder's decision pane)".
- **Handed up to the human rather than answered:**
  - `T1231`: "I can't pick this alone; it's a visual choice for the founder."
  - `T1219`: "I've put your two rewrites in front of the founder".
- **Builder results relayed by hand:**
  - `T1245`: "The basis is builder-50's check";
  - `T1445`: "Interim notes from the v2 builder (run)";
  - `T1350` (brand side): "the end-state finding is relayed from our builder's run".
- **The lead as a forwarder.** Every one of pptmstr's 7 acks says the message went on to a builder:
  - "Your rulings have all gone to the builder as given" (`T1330`);
  - "(b) has gone to the builder together with the end-frame ask" (`T1530`).

### What the parties were, by position (inferred from the above)

There were extra parties, so in that sense the run was a conversation of more than two parties squeezed through two
leads. **But no extra party was a third peer team.** They sat in two positions:
- **Above both leads:** one human, reachable by both directly. The relays of that human's answers are the plan's
  failure 2. Decided 3 (a decision on a channel is readable by every lead on it) is aimed at exactly this.
- **Below each lead:** that lead's own builders. The hand relays are failure 5, which proposal 7 targets.

Nothing in the run sat beside the two leads as an equal. Every option, O1 through O4, treats that case differently,
and this run has no instance of it.

## 4. What one extra hop would have cost (run, under a stated model)

The model: a coordinator lead between the two that receives a message and re-sends it. In this run the closest act is
an `ack`: read a message, hand it on, write. That is a lower bound, because a forward that has to choose its
recipient or reword the message costs more.

| | median | p90 |
|---|---|---|
| one hop (the ack latency) | 0.61 min | 0.89 min |
| one request → answer round trip through a coordinator (2 extra hops) | +1.22 min | +1.78 min |
| the same round trip today, with no coordinator (one hop each way) | 0.71 min | 1.81 min |
| over all 15 answered needs-answer threads | +18.2 min | +26.8 min, against a 233-min run |

Read this as: **a coordinator roughly triples the reply time of a needs-answer, from about 0.7 to about 1.9 min at
the median.** Over the run that adds about 8% of wall time (inferred from the run figures). Most of the run's time
went on work, not on waiting for replies. The 15 needs-answer replies took 12.9 min in total (the sum of the latencies listed in the appendix).

What this estimate leaves out (inferred):
- **Queueing at the coordinator.** The turn gap's p90 was 9 min and its maximum 18.5 min, so a lead was sometimes busy
  for minutes. A coordinator that serves several teams would add that queueing on top. A two-party run can't measure
  it.
- **Polling.** Up to about 20 s of each hop may be the poll interval. An in-process wake would remove it, so the hop
  cost under the proposed bus would be the model's own turn time.
- **Approvals.** A coordinator hop on a reviewed rung parks: decision 1, "each side's own rung governs". Its cost is
  then the operator's response time, which this run can't show, because nothing in it parked across the boundary.

## 5. What this evidence cannot speak to

- **One run, two leads, one human, one machine.** Nothing here was observed with N > 2 peers. So nothing here can show
  any of these:
  - group-channel behaviour (O1's per-recipient receipts and "answered");
  - fan-out (O4);
  - whether a coordinator garbles a forward (O2's provenance chain).
- **The traffic was client and supplier.** Work went one way. A symmetric pair, where both sides ask for work, or a
  triangle, could behave differently. This run can't separate "O3's directed edges fit" from "this pair happened to
  be directed".
- **Files and polling, not the bus.** The latency figures include the poll interval and the agents' file-writing
  habits. Under an in-process wake they would differ, probably downward (inferred).
- **mtimes are taken on trust.** The protocol says a message is never edited (read). An edit would move its mtime,
  and the script can't detect one.
- **The script uses the machine's local zone.** It ran on the run's own machine, in EDT.
- **One reader set the classification and the party tags.** No second reader checked them. The keyword scan is only a
  partial check: it catches a missed name, not a wrong class.
- **The protocol leaves no trace of the hand relays.** It can't show what a lead passed to its builders, so the
  "handed on to builder" counts rest on the lead saying so.
- **Not measurable from these folders:**
  - the plan's "about 20 re-arms";
  - its "three false wakes" (the 11 builder files are their likely source, not a count of them);
  - how long the human took to answer the relayed approvals.
- **Reproducibility.** The brand session is still writing into `.concerns/`. A later re-run will count new messages,
  and the script prints any it has no classification for under `unclassified`.

## Appendix: the script's output (run, 2026-10-17 17:25 EDT)

```
== 1. Messages (ordered by mtime)
  pptmstr-lead  protocol 23  pre-protocol 0  needs-answer:yes 10  by kind {'ack': 7, 'delivery': 7, 'reply': 6, 'request': 3}
  brand-lead    protocol 18  pre-protocol 1  needs-answer:yes  5  by kind {'delivery': 3, 'reply': 10, 'request': 5}
  total protocol messages 41; first 12:07:24, last 16:00:41, span 233.29 min
  pre-protocol: pptmstr-copy--lead--handoff.md by brand-lead at 2026-10-17 09:47:29
  non-message files written into each folder during the run window:
    pptmstr-lead's folder: 0
    brand-lead's folder: 11
      12:42:53  lander-v2--builder--result.md
      12:53:54  lander-pptmstr--builder--result.md
      13:03:23  lander-pm-2--builder--result.md
      13:09:52  pm-favicon-sheet--builder--result.md
      13:19:06  pm-favicon-wire--builder--result.md
      13:45:36  transition-protos--builder--result.md
      13:50:18  lander-video-wire--builder--result.md
      14:15:17  lander-spotlight--builder--result.md
      14:52:48  lander-v3-switch--builder--result.md
      15:28:21  lander-zoom--builder--result.md
      15:40:39  lander-v4-crops--builder--result.md

== 1a. Name stamp versus mtime (positive = name ahead of the real clock)
  pptmstr-lead  min -127.00  median  65.00  max 122.00 min
  brand-lead    min  -6.00  median   0.00  max   6.00 min
  name order versus mtime order: 39 of 41 positions differ

== 1b. Reply latency
  re: latency, reply mtime - named file mtime  n=38  min   0.36  p25   0.61  median   1.15  p75  18.43  p90  27.64  max 199.40  (minutes)
    of which kind=ack                          n= 7  min   0.36  p25   0.40  median   0.61  p75   0.78  p90   0.89  max   0.89  (minutes)
    of which other kinds                       n=31  min   0.39  p25   0.62  median   1.90  p75  21.15  p90  27.64  max 199.40  (minutes)
  first other-side write after each message    n=39  min   0.06  p25   0.61  median   0.98  p75   8.98  p90  23.48  max  27.30  (minutes)
    after needs-answer:yes messages            n=15  min   0.06  p25   0.55  median   0.71  p75   1.02  p90   1.81  max   1.90  (minutes)
  turn gap (end of one side's run -> other's)  n=29  min   0.06  p25   0.57  median   0.78  p75   1.61  p90   8.98  max  18.48  (minutes)
  turns (maximal same-side runs): 30; run lengths [1, 1, 1, 1, 2, 1, 2, 1, 2, 1, 2, 1, 1, 1, 2, 1, 1, 1, 2, 1, 1, 1, 2, 1, 1, 2, 2, 2, 1, 2]
  needs-answer:yes threads (first other-side message whose re: names it):
    12:07:24 20261017T1000--lander-shots--pptmstr-lead--reply.md -> 12:09:18 reply      1.90 min
    12:12:58 20261017T1212--lander-video--brand-lead--request.md -> 12:13:56 reply      0.97 min
    12:18:03 20261017T1235--lander-claims--pptmstr-lead--delivery.md -> 12:19:52 reply      1.81 min
    12:30:27 20261017T1320--lander-shots--pptmstr-lead--request.md -> 12:31:28 reply      1.02 min
    12:40:18 20261017T1345--lander-shots--pptmstr-lead--request.md -> 12:40:52 reply      0.57 min
    13:05:15 20261017T1410--lander-shots--pptmstr-lead--delivery.md -> 13:05:58 reply      0.71 min
    13:25:00 20261017T1324--lander-masters--brand-lead--request.md -> 13:25:38 reply      0.62 min
    13:40:36 20261017T1445--lander-video--pptmstr-lead--request.md -> 13:40:59 reply      0.39 min
    13:50:55 20261017T1350--lander-video--brand-lead--request.md -> 13:51:16 ack        0.36 min
    13:50:58 20261017T1520--lander-video--pptmstr-lead--delivery.md -> 13:52:15 reply      1.28 min
    13:53:30 20261017T1353--lander-masters--brand-lead--request.md -> 13:54:14 ack        0.74 min
    14:19:24 20261017T1600--lander-masters--pptmstr-lead--delivery.md -> 14:20:00 reply      0.61 min
    14:38:29 20261017T1640--lander-masters--pptmstr-lead--delivery.md -> 14:38:57 reply      0.46 min
    15:03:21 20261017T1503--lander-mobile--brand-lead--request.md -> 15:04:15 ack        0.89 min
    15:31:00 20261017T1530b--lander-mobile--pptmstr-lead--delivery.md -> 15:31:33 reply      0.55 min
  needs-answer:yes -> first reply naming it    n=15  min   0.36  p25   0.55  median   0.71  p75   1.02  p90   1.81  max   1.90  (minutes)
  [R5 added the section below to the script; output from the R5 run at 17:52 EDT]
  needs-answer:yes threads, first NON-ACK other-side reply naming it (R5):
    12:07:24 20261017T1000--lander-shots--pptmstr-lead--reply.md -> 12:09:18 reply      1.90 min
    12:12:58 20261017T1212--lander-video--brand-lead--request.md -> 12:13:56 reply      0.97 min
    12:18:03 20261017T1235--lander-claims--pptmstr-lead--delivery.md -> 12:19:52 reply      1.81 min
    12:30:27 20261017T1320--lander-shots--pptmstr-lead--request.md -> 12:31:28 reply      1.02 min
    12:40:18 20261017T1345--lander-shots--pptmstr-lead--request.md -> 12:40:52 reply      0.57 min
    13:05:15 20261017T1410--lander-shots--pptmstr-lead--delivery.md -> 13:05:58 reply      0.71 min
    13:25:00 20261017T1324--lander-masters--brand-lead--request.md -> 13:25:38 reply      0.62 min
    13:40:36 20261017T1445--lander-video--pptmstr-lead--request.md -> 13:40:59 reply      0.39 min
    13:50:55 20261017T1350--lander-video--brand-lead--request.md: NO non-ack reply names it
    13:50:58 20261017T1520--lander-video--pptmstr-lead--delivery.md -> 13:52:15 reply      1.28 min
    13:53:30 20261017T1353--lander-masters--brand-lead--request.md -> 14:19:24 delivery  25.90 min
    14:19:24 20261017T1600--lander-masters--pptmstr-lead--delivery.md -> 14:20:00 reply      0.61 min
    14:38:29 20261017T1640--lander-masters--pptmstr-lead--delivery.md -> 14:38:57 reply      0.46 min
    15:03:21 20261017T1503--lander-mobile--brand-lead--request.md -> 15:31:00 delivery  27.64 min
    15:31:00 20261017T1530b--lander-mobile--pptmstr-lead--delivery.md -> 15:31:33 reply      0.55 min
  needs-answer:yes -> first non-ack reply      n=14  min   0.39  p25   0.57  median   0.84  p75   1.81  p90  25.90  max  27.64  (minutes)
  needs-answer:yes with no non-ack reply naming it: 1

== 1c. Crossing candidates: a message whose re: does not name the other side's newest message before it (window 120 s flags likely crossings)
  12:18:03 20261017T1235--lander-claims--pptmstr-lead--delivery.md
      newest other-side before it: 12:12:58 20261017T1212--lander-video--brand-lead--request.md (  306 s earlier)
      its re: pptmstr-copy--lead--handoff.md  -> older thread / new topic
  12:30:27 20261017T1320--lander-shots--pptmstr-lead--request.md
      newest other-side before it: 12:19:52 20261017T1219--lander-claims--brand-lead--reply.md (  635 s earlier)
      its re: 20261017T1209--lander-shots--brand-lead--reply.md  -> older thread / new topic
  13:40:36 20261017T1445--lander-video--pptmstr-lead--request.md
      newest other-side before it: 13:25:00 20261017T1324--lander-masters--brand-lead--request.md (  935 s earlier)
      its re: 20261017T1212--lander-video--brand-lead--request.md  -> older thread / new topic
  13:50:58 20261017T1520--lander-video--pptmstr-lead--delivery.md
      newest other-side before it: 13:50:55 20261017T1350--lander-video--brand-lead--request.md (    4 s earlier)
      its re: 20261017T1340--lander-video--brand-lead--reply.md  -> LIKELY CROSSING
  13:52:15 20261017T1355--lander-video--brand-lead--reply.md
      newest other-side before it: 13:51:16 20261017T1525--lander-video--pptmstr-lead--ack.md (   59 s earlier)
      its re: 20261017T1520--lander-video--pptmstr-lead--delivery.md  -> LIKELY CROSSING
  13:53:30 20261017T1353--lander-masters--brand-lead--request.md
      newest other-side before it: 13:52:40 20261017T1530--lander-video--pptmstr-lead--ack.md (   50 s earlier)
      its re: 20261017T1430--lander-masters--pptmstr-lead--reply.md  -> LIKELY CROSSING
  15:37:27 20261017T1537--lander-copy--brand-lead--delivery.md
      newest other-side before it: 15:31:00 20261017T1530b--lander-mobile--pptmstr-lead--delivery.md (  388 s earlier)
      its re: 20261017T1235--lander-claims--pptmstr-lead--delivery.md  -> older thread / new topic
  16:00:41 20261017T1554--lander-assets--brand-lead--delivery.md
      newest other-side before it: 15:38:02 20261017T1545--lander-copy--pptmstr-lead--reply.md ( 1359 s earlier)
      its re: new  -> older thread / new topic
  likely crossings within window: 3

== 2. Classification (set by reading; CLASSIFY table in this script)
  side               request       answer     approval       status        other unclassified
  pptmstr-lead             4            9            0            9            1            0
  brand-lead               6            1            9            3            0            0

== 3. Parties other than the two leads (reading) beside a keyword count (mechanical)
  [42 per-message rows; re-run the script to see them]
  messages carrying at least one other party: 37 of 42
    builder          20
    builder:in       10
    builder:out      10
    founder          16
    founder:in       10
    founder:out      7
    investigator     1
    investigator:in  1
    operator         3
    operator:in      3
  requests whose authority is a relayed founder/operator yes: 5
    pptmstr-copy--lead--handoff.md
    20261017T1212--lander-video--brand-lead--request.md
    20261017T1324--lander-masters--brand-lead--request.md
    20261017T1353--lander-masters--brand-lead--request.md
    20261017T1503--lander-mobile--brand-lead--request.md

== 4. What one extra hop would have cost (model: a coordinator lead receives and re-sends,
   which in this run looks like an ack: read, hand on, write)
  ack latency median   0.61 min, p90   0.89 min
  a request -> answer round trip through one coordinator adds 2 hops: median   1.22 min, p90   1.78 min
  measured needs-answer round trip today (one hop each way): median   0.71 min
  over this run's 15 answered needs-answer threads: + 18.23 min at the median, + 26.77 min at p90, against a 233.29 min run
```
