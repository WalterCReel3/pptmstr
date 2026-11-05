# The first three-lead run: the links worked, and nobody could tell who was on the other end

**Dated:** 2026-10-24 · **Status:** case study. It records what was seen; the items it found are on
the board as round four's first tasks. · **Evidence:** the three sessions' transcripts for the run
(about 53 minutes, read by the lead with a script that merged them into one timeline). The
operator's words and the content of the work are deliberately left out. Only the coordination
is recorded.

## The setup

Three leads, each in its own project:
- an **infrastructure lead**, linked to both of the others, so it was the hub;
- a **research lead**, which had produced the infrastructure lead's original design. Its link had
  no charter;
- a **design lead**, which owned the content the infrastructure lead was to deploy. Its link had a
  one-line charter.

The two outer leads were never linked to each other. That is a star, the shape the topology
research called O2-star, and nobody designed it: the operator built it by linking.

| | Infrastructure (hub) | Research | Design |
|---|---|---|---|
| Messages sent | 17 | 6 | 6 |
| Substantive operator prompts | 11 | 4 | 2 |
| Restarted mid-run (model change) | yes | no | yes |

The run also used one channel decision, raised by the design lead and answered by the operator,
and eleven tasks on the hub's board.

## What worked

- **Delivery.** Every non-quiet message woke its recipient in about two seconds. Nothing stalled,
  and the K backstop never fired. This is the operator's primary concern from Decided 10,
  observed working.
- **The hub did coordinator work well.**
  - It carried the operator's answer to the channel decision into three running builders'
    tasks within half a minute.
  - It noticed it had asked the wrong peer and redirected the question.
  - It merged both peers' input into one record in its own project.
- **The channel decision** was answered in about 70 seconds and read by both leads, with nothing
  relayed (Decided 3, 12).
- **Quiet acknowledgements** were used as intended, and no exchange looped.

## What went wrong

1. **A lead can't tell who is on the other end of a link.**
   - The research lead, asked about its peer, could say only that a link existed and that nothing
     had arrived.
   - The hub sent a question meant for the design lead to the research lead, assuming from the
     similar channel names that the research lead owned the design. The research lead had to
     answer that it didn't.
   - Both sides invented names for each other.
   - A link tells a lead only a channel name, a charter (empty on one link here) and session ids.
     "Discovery is the link" held; the link just doesn't describe the peer.
2. **A question that was read but not answered slipped through a restart.**
   - The design lead read a needs-answer and started unrelated implementation instead of
     answering. It was then restarted to change its model.
   - The resume wake names only unread mail, so the question was never mentioned again. The
     operator had to go to that session and ask what was pending.
   - The NEEDS YOU row did fire for it. Which state raised it (the not-running window during the
     restart, most likely) can't be told from the transcripts alone.
3. **"Your peer" is ambiguous on a hub.** The operator asked the hub to re-ask a question of its
   peer, and the hub picked the wrong one of its two links. Nothing on screen showed which peer
   held the question.
4. **No way to withdraw a message.**
   - Asked to dismiss an open question, the hub sent a quiet "please disregard" message instead.
   - The question was reopened minutes later as a proposal, so the original was still open for
     the recipient the whole time.
5. **The operator routed around missing visibility.**
   - Twice the operator went to an outer lead directly: once to have it send its reasons to the
     hub, and once to find out what it owed.
   - Neither was a missing coordinator. Both were missing views of whose move it was.
6. **Redundant relay.** After the decision was answered, each lead messaged the other the answer,
   though both could already read it.

## What it says about a coordinator

See [the coordinator concept](2026-10-21-a-coordinator-session-holds-a-mandate.md). This is the
first evidence on the axis the topology research never measured: the operator's load.

- **For a coordinator (H2).**
  - Given links, the operator built a hub by hand, and the hub carried real coordination load.
  - The operator was still the router *for the hub*: most prompts went to it, and most of those
    told it whom to ask about what.
- **For visibility first (H1).**
  - Findings 1–5 would hit a dedicated coordinator the same way:
    - not knowing who a peer is;
    - "your peer" with two links;
    - owed answers dropped on resume;
    - no view of whose move it is.
  - A coordinator on today's links would have misaddressed the same question.

**Reading:** the hub role is real, and operators will build it whether or not pptmstr names it.
What it needs first is links that describe their peers, and a whose-move view. A dedicated
coordinator role is worth designing after that, starting from this run's shape: one hub, links
only to it, and the operator still in the loop.

## Not checked

- The board log for the run wasn't read, so the NEEDS YOU row's exact state is inferred.
- One run, three leads, under an hour. Nothing here is a rate.
- Message timing comes from transcript timestamps, not from the store.
